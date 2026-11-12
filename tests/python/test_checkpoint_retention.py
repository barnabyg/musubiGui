import json
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from helpers import acceptance_workspace, wait_for

from musubi_gui.jobs import JobConfig, JobDraft, JobStore
from musubi_gui.recipe import Stage
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings
from musubi_gui.snapshots import digest_file


def test_slow_retention_validation_does_not_block_force_stop_or_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    marker = tmp_path / "probe-started"
    original_popen = subprocess.Popen

    def delayed_probe(args: list[str], **kwargs: object) -> subprocess.Popen:
        if any(str(arg).endswith("state_probe.py") for arg in args):
            code = (
                f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(120)"
            )
            args = [sys.executable, "-c", code]
        return original_popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", delayed_probe)
    service, run, output = submitted_history(tmp_path, save_state=True)
    service.close()
    # The local execution adapter supplies a slow, CPU-only training process and real state-probe boundary.
    script = tmp_path / "trainer.py"
    script.write_text("import time\nprint('training ready', flush=True)\ntime.sleep(120)\n", encoding="utf-8")
    previous = service.get(run["id"])
    plan = ExecutionPlan(
        job=previous["job"],
        settings=Settings(checkout=str(tmp_path), python=sys.executable),
        manifest={},
        directory=Path(previous["directory"]),
        cache=tmp_path / "cache",
        fingerprint="slow",
        stages=[
            Stage(
                "train",
                [
                    sys.executable,
                    str(script),
                    "--output_name",
                    "lora",
                    "--output_dir",
                    str(output),
                    "--max_train_steps",
                    "4",
                ],
            )
        ],
    )
    service = RunService(tmp_path / "slow-state", fixture_mode=False, device_path=tmp_path / "gpu.sqlite")
    try:
        active = service.submit(plan, start_after_prepare=True)
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), service.get(active["id"])
        probe_pid = int(marker.read_text())
        service.stop(active["id"])
        assert service.get(active["id"])["status"] == "stopping"
        service.stop(active["id"], force=True, confirmed=True)
        deadline = time.monotonic() + 15
        while service.get(active["id"])["status"] != "stopped" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert service.get(active["id"])["status"] == "stopped"
        service.close()
        assert not psutil.pid_exists(probe_pid)
        assert not any(thread.name == "musubi-retention" and thread.is_alive() for thread in threading.enumerate())
    finally:
        service.close()
        if service.get(active["id"]).get("processes"):
            recovered = service.reconcile(active["id"])
            if recovered["status"] in {"orphaned", "uncertain"}:
                service.terminate_orphan(active["id"], confirmed=True)


def test_retention_prunes_old_saves_but_preserves_the_final_restorable_pair(tmp_path: Path) -> None:
    workspace = acceptance_workspace(tmp_path)
    root = Path(workspace["state"])
    draft = next(job for job in workspace["jobs"] if job["family"] == "wan")
    job = JobStore(root).create(
        JobDraft.model_validate(
            {
                "name": "Retained pair",
                "family": "wan",
                "dataset_id": draft["dataset_id"],
                "config": {
                    "recipe": "alternating",
                    "steps": 8,
                    "expert_interval": 2,
                    "save_state": True,
                    "save_every": 1,
                    "keep_checkpoints": 1,
                },
            }
        )
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job.id, start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        states = service.saved_states(run["id"])
        completed = service.get(run["id"])
        assert completed["retention"]["removed"]
        assert completed["retention"]["errors"] == []
        assert all(Path(state["path"]).is_dir() for state in completed["expert_states"].values())
        assert all(state["valid"] for state in states), states
        assert states[-1]["valid"] and states[-1]["counts"] == {"low": 4, "high": 4}
        assert all("missing or moved" not in state["error"] for state in states)
        restored = service.restore_state(run["id"], states[-1]["id"], 4, confirmed=True)
        finished = wait_for(service, restored["id"], "completed", device_released=True)
        assert finished["expert_states"]["high"]["optimizer_step"] == 6
    finally:
        service.close()


def test_old_jobs_keep_unlimited_checkpoints() -> None:
    assert JobConfig().model_dump()["keep_checkpoints"] == 0


def checkpoint(file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"weight": {"dtype": "U8", "shape": [4], "data_offsets": [0, 4]}}).encode()
    file.write_bytes(len(header).to_bytes(8, "little") + header + b"data")


def submitted_history(tmp_path: Path, *, save_state: bool = False) -> tuple[RunService, dict, Path]:
    service = RunService(tmp_path / "state", fixture_mode=True, autostart=False, device_path=tmp_path / "gpu.sqlite")
    directory = tmp_path / "output" / "runs" / "one"
    plan = ExecutionPlan(
        job={"id": "one", "revision": 1, "family": "h3", "config": {"save_state": save_state, "keep_checkpoints": 1}},
        settings=Settings(checkout=str(tmp_path), python=sys.executable, output_root=str(tmp_path / "output")),
        manifest={"items": []},
        directory=directory,
        cache=tmp_path / "cache",
        fingerprint="one",
        stages=[
            Stage(
                "train",
                [
                    "trainer",
                    "--output_name",
                    "lora",
                    "--output_dir",
                    str(directory / "checkpoints"),
                    "--max_train_steps",
                    "4",
                ],
            )
        ],
    )
    run = service.submit(plan)
    output = directory / "checkpoints"
    for step in (1, 2, 3):
        file = output / f"lora-step{step:08d}.safetensors"
        checkpoint(file)
        if save_state:
            state = file.with_name(file.stem + "-state")
            state.mkdir()
            checkpoint(state / "model.safetensors")
            for name in ("optimizer.bin", "scheduler.bin", "random_states_0.pkl"):
                (state / name).write_bytes(b"synthetic")
            (state / "trainer_state.json").write_text(
                json.dumps({"version": 1, "synthetic": True, "optimizer_step": step}), encoding="utf-8"
            )
    return service, run, output


def test_incomplete_new_save_never_authorizes_pruning(tmp_path: Path) -> None:
    service, run, output = submitted_history(tmp_path)
    (output / "lora-step00000003.safetensors").write_bytes(b"unfinished")
    service.retain_checkpoints(run["id"])
    assert (output / "lora-step00000001.safetensors").is_file()
    assert service.get(run["id"])["retention"]["removed"] == []


def test_retention_before_first_checkpoint_does_not_report_a_missing_path(tmp_path: Path) -> None:
    service, run, output = submitted_history(tmp_path)
    try:
        # Real trainers create this folder at their first save, after caching and startup.
        shutil.rmtree(output)
        service.retain_checkpoints(run["id"])
        status = service.get(run["id"])["retention"]
        assert status["errors"] == [], status
        assert status["pending"] == [] and status["removed"] == []
        # An initially empty retention pass must not prevent pruning later saves.
        for step in (1, 2, 3):
            checkpoint(output / f"lora-step{step:08d}.safetensors")
        service.retain_checkpoints(run["id"])
        status = service.get(run["id"])["retention"]
        assert status["errors"] == []
        assert status["removed"] == ["lora-step00000001.safetensors"]
        assert (output / "lora-step00000003.safetensors").is_file()
    finally:
        service.close()


def test_incomplete_optimizer_state_never_authorizes_pruning(tmp_path: Path) -> None:
    service, run, output = submitted_history(tmp_path, save_state=True)
    (output / "lora-step00000003-state" / "scheduler.bin").unlink()
    service.retain_checkpoints(run["id"])
    assert (output / "lora-step00000001-state").is_dir()
    assert service.get(run["id"])["retention"]["removed"] == []


def test_final_interval_alias_does_not_consume_a_historical_save_slot(tmp_path: Path) -> None:
    service, run, output = submitted_history(tmp_path)
    checkpoint(output / "lora-step00000004.safetensors")
    checkpoint(output / "lora.safetensors")
    service.retain_checkpoints(run["id"])
    assert (output / "lora-step00000003.safetensors").is_file()
    assert (output / "lora.safetensors").is_file()
    assert not (output / "lora-step00000002.safetensors").exists()


def test_prepared_restoration_inputs_are_protected_from_pruning(tmp_path: Path) -> None:
    service, run, output = submitted_history(tmp_path)
    old = output / "lora-step00000001.safetensors"
    dependent = ExecutionPlan(
        job={"id": "dependent", "revision": 1, "family": "h3"},
        settings=Settings(),
        manifest={},
        directory=tmp_path / "dependent",
        cache=tmp_path / "dependent-cache",
        stages=[],
        fingerprint="dependent",
        input_files={str(old): digest_file(old)},
    )
    service.submit(dependent)
    service.retain_checkpoints(run["id"])
    assert old.is_file()
    assert old.name in service.get(run["id"])["retention"]["protected"]


def test_failed_pruning_is_hidden_and_retried_without_losing_newer_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, run, output = submitted_history(tmp_path, save_state=True)
    remove = shutil.rmtree

    def locked(path: Path) -> None:
        raise PermissionError("scanner holds file")

    monkeypatch.setattr(shutil, "rmtree", locked)
    service.retain_checkpoints(run["id"])
    status = service.get(run["id"])["retention"]
    assert status["errors"] and status["pending"] == ["lora-step00000001-state"]
    assert not (output / "lora-step00000001-state").exists()
    assert (output / "lora-step00000003-state").is_dir()
    monkeypatch.setattr(shutil, "rmtree", remove)
    service.close()
    service = RunService(tmp_path / "state", fixture_mode=True, autostart=False, device_path=tmp_path / "gpu.sqlite")
    service.retain_checkpoints(run["id"])
    recovered = service.get(run["id"])["retention"]
    assert recovered["errors"] == [] and recovered["pending"] == []
    assert not (output / ".retention" / "lora-step00000001-state").exists()

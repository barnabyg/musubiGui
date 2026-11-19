import json
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from helpers import acceptance_workspace, wait_for

from musubi_gui.job_models import JobConfig, JobDraft
from musubi_gui.job_store import JobStore
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
                    # Saves after every update, between switches and at them, leave two old pairs to prune.
                    "steps": 3,
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
        assert states[-1]["valid"] and states[-1]["counts"] == {"low": 3, "high": 3}
        assert all("missing or moved" not in state["error"] for state in states)
        restored = service.restore_state(run["id"], states[-1]["id"], states[-1]["resume_step"] + 2, confirmed=True)
        finished = wait_for(service, restored["id"], "completed", device_released=True)
        assert finished["expert_states"]["high"]["optimizer_step"] == 5
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
                    str(directory),
                    "--max_train_steps",
                    "4",
                ],
            )
        ],
    )
    run = service.submit(plan)
    output = directory
    for step in (1, 2, 3):
        file = output / f"lora-step{step:08d}.safetensors"
        checkpoint(file)
        if save_state:
            state = output / "state" / (file.stem + "-state")
            state.mkdir(parents=True)
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
        for file in output.glob("*.safetensors"):
            file.unlink()
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
    (output / "state" / "lora-step00000003-state" / "scheduler.bin").unlink()
    service.retain_checkpoints(run["id"])
    assert (output / "state" / "lora-step00000001-state").is_dir()
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
    assert not (output / "state" / "lora-step00000001-state").exists()
    assert (output / "state" / "lora-step00000003-state").is_dir()
    monkeypatch.setattr(shutil, "rmtree", remove)
    service.close()
    service = RunService(tmp_path / "state", fixture_mode=True, autostart=False, device_path=tmp_path / "gpu.sqlite")
    service.retain_checkpoints(run["id"])
    recovered = service.get(run["id"])["retention"]
    assert recovered["errors"] == [] and recovered["pending"] == []
    assert not (output / "logs" / ".retention" / "lora-step00000001-state").exists()


def saves(output: Path, steps: tuple[int, ...]) -> None:
    for step in steps:
        file = output / f"lora-step{step:08d}.safetensors"
        checkpoint(file)
        state = output / "state" / (file.stem + "-state")
        state.mkdir(parents=True)
        checkpoint(state / "model.safetensors")
        for name in ("optimizer.bin", "scheduler.bin", "random_states_0.pkl"):
            (state / name).write_bytes(b"synthetic")
        (state / "trainer_state.json").write_text(
            json.dumps({"version": 1, "synthetic": True, "optimizer_step": step - 3}), encoding="utf-8"
        )


def resumed_segment(
    tmp_path: Path, service: RunService, source: dict, *, keep: int = 1, legacy: bool = False
) -> tuple[dict, Path]:
    """A segment resumed from the source's state at step 3, saving at global steps 4, 5 and 6."""
    directory = tmp_path / "output" / "runs" / "two"
    plan = ExecutionPlan(
        job={"id": "one", "revision": 2, "family": "h3", "config": {"save_state": True, "keep_checkpoints": keep}},
        settings=Settings(checkout=str(tmp_path), python=sys.executable, output_root=str(tmp_path / "output")),
        manifest={"items": []},
        directory=directory,
        cache=tmp_path / "cache",
        fingerprint="two",
        stages=[
            Stage(
                "train",
                [
                    "trainer",
                    "--output_name",
                    "lora",
                    "--output_dir",
                    str(directory),
                    "--max_train_steps",
                    "4",
                ],
            )
        ],
    )
    run = service.submit(plan)
    resumed = Path(source["directory"]) / "state" / "lora-step00000003-state"
    segment = {"additional_steps": 4} if legacy else {"step_offset": 3, "target_steps": 7, "additional_steps": 4}
    run = service.store.update(
        run["id"],
        lambda record: record.update(
            status="completed",
            resume_of=source["id"],
            segment=segment,
            state_source={"path": str(resumed), "files": {}},
        ),
    )
    output = directory
    saves(output, (1, 2, 3) if legacy else (4, 5, 6))
    return run, output


def test_retention_counts_a_chain_by_global_step_and_keeps_the_state_a_segment_resumed_from(tmp_path: Path) -> None:
    service, source, first = submitted_history(tmp_path, save_state=True)
    try:
        service.store.update(source["id"], lambda record: record.update(status="stopped"))
        segment, second = resumed_segment(tmp_path, service, source)
        service.retain_checkpoints(segment["id"])
        # One historical save plus the newest across the chain: steps 5 and 6 of the segment.
        assert sorted(path.name for path in second.glob("*.safetensors")) == [
            "lora-step00000005.safetensors",
            "lora-step00000006.safetensors",
        ]
        assert [path.name for path in first.glob("*.safetensors")] == ["lora-step00000003.safetensors"]
        # The segment resumed from step 3, so that save and its state stay beyond the limit.
        assert (first / "state" / "lora-step00000003-state").is_dir()
        assert not (first / "state" / "lora-step00000001-state").exists()
        status = service.get(source["id"])["retention"]
        assert sorted(status["removed"]) == sorted(
            [f"lora-step0000000{step}{suffix}" for step in (1, 2) for suffix in (".safetensors", "-state")]
        )
        assert set(status["protected"]) == {"lora-step00000003.safetensors", "lora-step00000003-state"}
        assert service.get(segment["id"])["retention"]["removed"] == [
            "lora-step00000004.safetensors",
            "lora-step00000004-state",
        ]
        # Inspecting the source's saved states applies the chain's count too, and keeps the resumed state.
        names = [state["name"] for state in service.saved_states(source["id"]) if state["valid"]]
        assert names == ["lora-step00000003-state"]
    finally:
        service.close()


def test_the_latest_segment_sets_the_chains_limit(tmp_path: Path) -> None:
    service, source, first = submitted_history(tmp_path, save_state=True)
    try:
        service.store.update(source["id"], lambda record: record.update(status="stopped"))
        segment, second = resumed_segment(tmp_path, service, source, keep=0)
        service.retain_checkpoints(segment["id"])
        service.retain_checkpoints(source["id"])
        assert len(list(first.glob("*.safetensors"))) == 3
        assert len(list(second.glob("*.safetensors"))) == 3
    finally:
        service.close()


def test_a_chain_with_a_segment_restored_before_continuous_numbering_counts_each_segment(tmp_path: Path) -> None:
    service, source, first = submitted_history(tmp_path, save_state=True)
    try:
        service.store.update(source["id"], lambda record: record.update(status="stopped"))
        segment, second = resumed_segment(tmp_path, service, source, legacy=True)
        service.retain_checkpoints(segment["id"])
        assert sorted(path.name for path in second.glob("*.safetensors")) == [
            "lora-step00000002.safetensors",
            "lora-step00000003.safetensors",
        ]
        # The source is pruned only by its own count, which keeps its last two saves.
        assert len(list(first.glob("*.safetensors"))) == 3
        service.retain_checkpoints(source["id"])
        assert sorted(path.name for path in first.glob("*.safetensors")) == [
            "lora-step00000002.safetensors",
            "lora-step00000003.safetensors",
        ]
    finally:
        service.close()

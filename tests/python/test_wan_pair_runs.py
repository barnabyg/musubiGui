import time
from pathlib import Path

import pytest
from helpers import acceptance_workspace, wait_for

from musubi_gui.jobs import JobConfig, JobDataset, JobDraft, JobStore, configuration_errors, review_job
from musubi_gui.outputs import OutputBrowser
from musubi_gui.runs import RunService
from musubi_gui.settings import load_settings
from musubi_gui.wan_pair_plan import argument
from musubi_gui.wan_pair_runtime import finish_phase


def paired_job(tmp_path: Path, **config: object) -> tuple[Path, str]:
    workspace = acceptance_workspace(tmp_path)
    root = Path(workspace["state"])
    dataset = next(job["dataset_id"] for job in workspace["jobs"] if job["family"] == "wan")
    job = JobStore(root).create(
        JobDraft(
            name="Paired Wan",
            datasets=[JobDataset(dataset_id=dataset)],
            config=JobConfig.model_validate(
                {"recipe": "alternating", "steps": 8, "expert_interval": 2, "save_state": True, **config}
            ),
        )
    )
    return root, job.id


def test_review_training_both_experts_and_sampling_the_exact_pair(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path)
    settings = load_settings(root)
    job = JobStore(root).get(job_id)
    review = review_job(root, job, settings, fixture_mode=True)
    assert review["valid"], review["errors"]
    assert review["expert_schedule"]["counts"] == {"low": 4, "high": 4}
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        phases = [stage for stage in completed["stages"] if stage.get("training")]
        assert [stage["training"]["expert"] for stage in phases] == ["low", "high", "low", "high"]
        assert ["--resume" in stage["args"] for stage in phases] == [False, False, True, True]
        for stage in phases:
            expert = stage["training"]["expert"]
            assert argument(stage["args"], "--dit") == settings.models[f"wan_{expert}"]
            assert argument(stage["args"], "--min_timestep") == ("0" if expert == "low" else "875")
            assert argument(stage["args"], "--max_timestep") == ("875" if expert == "low" else "1000")
        assert {expert: state["optimizer_step"] for expert, state in completed["expert_states"].items()} == {
            "low": 4,
            "high": 4,
        }
        assert completed["sample_pair"]["low"]["path"] == phases[2]["training"]["checkpoint"]
        assert completed["sample_pair"]["high"]["path"] == phases[3]["training"]["checkpoint"]
        assert completed["samples"][0]["lora_pair"] == completed["sample_pair"]
        assert service.progress(run["id"])["expert_steps"] == {"low": 4, "high": 4}
        outputs = OutputBrowser(service).scan(run["id"])
        assert outputs["lora_pair"]["low"]["output"]["status"] == "available"
        assert outputs["lora_pair"]["high"]["output"]["status"] == "available"
        states = service.saved_states(run["id"])
        assert states and all(state["valid"] for state in states)
        assert states[-1]["counts"] == {"low": 4, "high": 4}
        # Restore from inside the first high phase, not its final save.
        source = next(state for state in states if state["schedule_step"] == 3)
        restored = service.restore_state(run["id"], source["id"], 5, confirmed=True)
        resumed = wait_for(service, restored["id"], "completed", device_released=True)
        assert resumed["segment"]["schedule_start"] == 3
        assert [
            (stage["training"]["expert"], stage["training"]["steps"])
            for stage in resumed["stages"]
            if stage.get("training")
        ] == [("high", 1), ("low", 2), ("high", 2)]
        assert {expert: state["optimizer_step"] for expert, state in resumed["expert_states"].items()} == {
            "low": 4,
            "high": 4,
        }
    finally:
        service.close()


@pytest.mark.parametrize(
    "config,message",
    [
        ({"expert_interval": 0}, "Expert interval:"),
        ({"expert_interval": 8}, "Steps:"),
        ({"save_state": False}, "Training state:"),
    ],
)
def test_invalid_pair_cannot_prepare(tmp_path: Path, config: dict[str, object], message: str) -> None:
    root, job_id = paired_job(tmp_path, **config)
    errors = configuration_errors(JobStore(root).get(job_id))
    assert any(error.startswith(message) for error in errors)


def test_changed_expert_state_or_checkpoint_is_not_restorable(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=4, expert_interval=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        states = service.saved_states(run["id"])
        source = states[-1]
        path = Path(source["sources"]["low"]["path"]) / "scheduler.bin"
        path.write_text("changed", encoding="utf-8")
        with pytest.raises(ValueError, match="incomplete or changed"):
            service.restore_state(run["id"], source["id"], 2, confirmed=True)
        assert not service.saved_states(run["id"])[-1]["valid"]
        assert completed["status"] == "completed"
    finally:
        service.close()


def test_stop_at_switch_keeps_gpu_ownership_and_restores_both_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, job_id = paired_job(tmp_path, steps=12, expert_interval=3)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    stopped_once = False

    def stop_after_high(run: dict, stage: dict) -> dict:
        nonlocal stopped_once
        result = finish_phase(run, stage)
        assert service.device.owner()["run_id"] == run["id"]
        if not stopped_once and stage["training"]["expert"] == "high":
            stopped_once = True
            service.stop(run["id"])
        return result

    monkeypatch.setattr("musubi_gui.runs.finish_phase", stop_after_high)
    try:
        run = service.prepare(job_id, start=True)
        stopped = wait_for(service, run["id"], "stopped", device_released=True)
        phases = [stage for stage in stopped["stages"] if stage.get("training")]
        assert [stage["status"] for stage in phases] == ["completed", "completed", "pending", "pending"]
        states = service.saved_states(run["id"])
        source = next(state for state in states if state["schedule_step"] == 6)
        restored = service.restore_state(run["id"], source["id"], 6, confirmed=True)
        completed = wait_for(service, restored["id"], "completed", device_released=True)
        assert {expert: state["optimizer_step"] for expert, state in completed["expert_states"].items()} == {
            "low": 6,
            "high": 6,
        }
        assert completed["sample_pair"]["high"]["steps"] == 6
    finally:
        service.close()


def test_stop_inside_first_phase_restores_partial_phase_before_starting_high(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=24, expert_interval=6)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        deadline = time.monotonic() + 180
        checkpoint = None
        while time.monotonic() < deadline:
            checkpoint = next(
                (Path(run["directory"]) / "checkpoints").glob("*-step00000001-state/trainer_state.json"), None
            )
            if checkpoint and checkpoint.is_file():
                break
            time.sleep(0.02)
        assert checkpoint and checkpoint.is_file()
        service.stop(run["id"], force=True, confirmed=True)
        wait_for(service, run["id"], "stopped", device_released=True)
        states = service.saved_states(run["id"])
        source = next(state for state in states if state["schedule_step"] == 1)
        assert source["valid"] and source["counts"] == {"low": 1, "high": 0}
        restored = service.restore_state(run["id"], source["id"], 11, confirmed=True)
        completed = wait_for(service, restored["id"], "completed", device_released=True)
        assert completed["stages"][0]["training"]["steps"] == 5
        assert completed["sample_pair"]["low"]["steps"] == completed["sample_pair"]["high"]["steps"] == 6
        assert "--resume" not in completed["stages"][1]["args"]
    finally:
        service.close()


def test_failure_does_not_launch_next_phase_or_sample_and_retry_does_not_reset_experts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, job_id = paired_job(tmp_path, steps=4, expert_interval=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")

    def corrupt_after_low(run: dict, stage: dict) -> dict:
        result = finish_phase(run, stage)
        if stage["training"]["start"] == 0:
            Path(result["path"], "scheduler.bin").write_text("changed after sealing", encoding="utf-8")
        return result

    monkeypatch.setattr("musubi_gui.runs.finish_phase", corrupt_after_low)
    try:
        run = service.prepare(job_id, start=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            failed = service.get(run["id"])
            if failed["status"] == "failed" and service.device.owner() is None:
                break
            time.sleep(0.02)
        assert failed["status"] == "failed", failed
        assert "changed" in failed["error"]
        assert failed["stages"][-1]["status"] == "pending"
        with pytest.raises(ValueError, match="Inspect paired saved states"):
            service.retry(run["id"])
        assert not service.saved_states(run["id"])[-1]["valid"]
    finally:
        service.close()


def test_one_update_restoration_still_publishes_both_loras(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=4, expert_interval=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        wait_for(service, run["id"], "completed", device_released=True)
        source = next(state for state in service.saved_states(run["id"]) if state["schedule_step"] == 4)
        restored = service.restore_state(run["id"], source["id"], 1, confirmed=True)
        wait_for(service, restored["id"], "completed", device_released=True)
        outputs = OutputBrowser(service).scan(restored["id"])
        assert outputs["lora_pair"]["low"]["steps"] == 3
        assert outputs["lora_pair"]["high"]["steps"] == 2
        assert all(outputs["lora_pair"][expert]["output"]["status"] == "available" for expert in ("low", "high"))
    finally:
        service.close()


def test_missing_old_phase_does_not_invalidate_a_later_complete_pair(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=4, expert_interval=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        wait_for(service, run["id"], "completed", device_released=True)
        states = service.saved_states(run["id"])
        early = next(state for state in states if state["schedule_step"] == 1)
        Path(early["sources"]["low"]["path"], "model.safetensors").unlink()
        inspected = service.saved_states(run["id"])
        assert not next(state for state in inspected if state["schedule_step"] == 1)["valid"]
        assert next(state for state in inspected if state["schedule_step"] == 4)["valid"]
    finally:
        service.close()

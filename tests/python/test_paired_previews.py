import time
from pathlib import Path

import pytest
from helpers import wait_for
from test_wan_pair_runs import paired_job

from musubi_gui.outputs import OutputBrowser
from musubi_gui.runs import RunService
from musubi_gui.settings import load_settings
from musubi_gui.wan_pair import alternating_phases


def test_preview_boundaries_preserve_total_expert_allocation() -> None:
    phases = alternating_phases(500, 30, preview_every=100)
    assert sum(phase.steps for phase in phases if phase.expert == "low") == 260
    assert sum(phase.steps for phase in phases if phase.expert == "high") == 240
    ends = {phase.start + phase.steps for phase in phases}
    assert {100, 200, 300, 400, 500} <= ends
    assert [(p.start, p.steps) for p in alternating_phases(4, 5, start=3, preview_every=4)] == [(3, 1), (4, 1), (5, 2)]
    with pytest.raises(ValueError, match="Preview interval"):
        alternating_phases(10001, 10001, preview_every=1)


@pytest.mark.parametrize("final_sample", [False, True])
def test_paired_previews_use_the_pair_at_each_total_step_and_restore_the_schedule(
    tmp_path: Path, final_sample: bool
) -> None:
    root, job_id = paired_job(tmp_path, steps=8, expert_interval=3, preview_every=2, sample_enabled=final_sample)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        generations = [stage for stage in completed["stages"] if stage["name"] == "generate"]
        assert [stage.get("preview_step") for stage in generations] == [2, 4, 6, 8] + ([None] if final_sample else [])
        assert all("--sample_every_n_steps" not in stage["args"] for stage in completed["stages"])
        samples = [sample for sample in completed["samples"] if "preview_step" in sample]
        assert len(completed["samples"]) == 4 + int(final_sample)
        assert [
            (sample["preview_step"], sample["lora_pair"]["low"]["steps"], sample["lora_pair"]["high"]["steps"])
            for sample in samples
        ] == [(2, 2, 0), (4, 3, 1), (6, 3, 3), (8, 5, 3)]
        assert samples[0]["lora_pair"]["high"]["base"] is True
        assert len({sample["path"] for sample in samples}) == 4
        outputs = OutputBrowser(service).scan(run["id"])
        previews = [output for output in outputs["outputs"] if "preview" in output.get("metadata", {})]
        assert len(previews) == 4
        assert all(output["status"] == "available" for output in previews)
        assert previews[0]["metadata"]["lora_pair"] == samples[0]["lora_pair"]
        state = next(state for state in service.saved_states(run["id"]) if state["schedule_step"] == 3)
        restored = service.restore_state(run["id"], state["id"], 5, confirmed=True)
        resumed = wait_for(service, restored["id"], "completed", device_released=True)
        assert [sample["preview_step"] for sample in resumed["samples"] if "preview_step" in sample] == [4, 6, 8]
        assert resumed["expert_states"]["low"]["optimizer_step"] == 5
        assert resumed["expert_states"]["high"]["optimizer_step"] == 3
    finally:
        service.close()


def test_failed_paired_preview_stops_before_the_next_training_phase(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=8, expert_interval=3, preview_every=2)
    (Path(load_settings(root).checkout) / "fail-generate").touch()
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        failed = wait_for(service, run["id"], "failed", device_released=True)
        assert failed["expert_states"]["low"]["optimizer_step"] == 2
        failed_index = next(index for index, stage in enumerate(failed["stages"]) if stage["status"] == "failed")
        assert failed["stages"][failed_index]["preview_step"] == 2
        assert all(stage["status"] == "pending" for stage in failed["stages"][failed_index + 1 :])
        assert not failed.get("samples")
    finally:
        service.close()


def test_retention_preserves_paired_previews_and_latest_restoration(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=12, expert_interval=3, preview_every=2, keep_checkpoints=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        wait_for(service, run["id"], "completed", device_released=True)
        states = service.saved_states(run["id"])
        assert states and all(state["valid"] for state in states)
        assert service.get(run["id"])["retention"]["removed"]
        outputs = OutputBrowser(service).scan(run["id"])
        previews = [output for output in outputs["outputs"] if "preview" in output.get("metadata", {})]
        assert [output["metadata"]["preview"]["step"] for output in previews] == [2, 4, 6, 8, 10, 12]
        assert all(output["status"] == "available" for output in previews)
        assert all(outputs["lora_pair"][expert]["output"]["status"] == "available" for expert in ("low", "high"))
        restored = service.restore_state(run["id"], states[-1]["id"], 6, confirmed=True)
        completed = wait_for(service, restored["id"], "completed", device_released=True)
        assert completed["expert_states"]["low"]["optimizer_step"] == 9
        assert completed["expert_states"]["high"]["optimizer_step"] == 9
    finally:
        service.close()


def test_stopping_after_a_paired_preview_retains_it_and_its_pair(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=40, expert_interval=10, preview_every=5)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = service.get(run["id"])
            if current.get("samples") and current["status"] == "training":
                break
            assert current["status"] not in {"failed", "completed"}, current
            time.sleep(0.02)
        else:
            raise AssertionError(service.get(run["id"]))
        service.stop(run["id"])
        stopped = wait_for(service, run["id"], "stopped", device_released=True)
        assert stopped["samples"][0]["preview_step"] == 5
        output = next(
            item
            for item in OutputBrowser(service).scan(run["id"])["outputs"]
            if item.get("metadata", {}).get("preview", {}).get("step") == 5
        )
        assert output["status"] == "available"
        assert output["metadata"]["lora_pair"]["low"]["steps"] == 5
    finally:
        service.close()

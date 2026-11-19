"""Save now, Sample now and Save and stop on CPU fixture Runs."""

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import acceptance_workspace, instant_steps, wait_for
from test_wan_pair_runs import paired_job

from musubi_gui import run_control
from musubi_gui.app import create_app
from musubi_gui.job_models import JobDraft
from musubi_gui.job_store import JobStore
from musubi_gui.outputs import OutputBrowser
from musubi_gui.run_control import TRAINING_FINISHED, unavailable
from musubi_gui.run_stage_outputs import record_checkpoints
from musubi_gui.run_store import RunStore
from musubi_gui.runs import RunService
from musubi_gui.trainer_control import HANDLED_FILE, REQUEST_FILE, write_json


def job_with(tmp_path: Path, family: str = "wan", **config: Any) -> tuple[Path, str]:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    jobs = JobStore(root)
    job = jobs.get(next(item["id"] for item in fixture["jobs"] if item["family"] == family))
    job = jobs.save(
        job.id,
        JobDraft(name=job.name, family=job.family, datasets=job.datasets, config=job.config.model_copy(update=config)),
        job.revision,
    )
    return root, job.id


def wait_until(service: RunService, run_id: str, ready: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if ready(run):
            return run
        if run["status"] in {"failed", "completed", "stopped"}:
            raise AssertionError(run)
        time.sleep(0.02)
    raise AssertionError(service.get(run_id))


def training(service: RunService, run_id: str) -> None:
    wait_until(service, run_id, lambda run: run["status"] == "training")


def handled(service: RunService, run_id: str, count: int) -> dict[str, Any]:
    run = wait_until(service, run_id, lambda run: len(run.get("controls", [])) >= count)
    return dict(run["controls"][count - 1])


def no_request_files(run: dict[str, Any]) -> bool:
    return not any((Path(run["directory"]) / "logs" / name).exists() for name in (REQUEST_FILE, HANDLED_FILE))


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_save_and_stop_leaves_a_restorable_state_at_the_stop_step(tmp_path: Path, family: str) -> None:
    # The Job keeps no state and no interval save falls before the stop; Save and stop still saves its state.
    root, job_id = job_with(tmp_path, family, steps=20, save_every=20, save_state=False, sample_enabled=True)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        service.control(run["id"], "save_and_stop")
        with pytest.raises(ValueError, match="Wait for Save and stop"):
            service.control(run["id"], "save")
        stopped = wait_for(service, run["id"], "stopped", device_released=True)
        step = stopped["controls"][0]["step"]
        assert 0 < step < 20
        assert stopped["stop_kind"] == "save"
        assert stopped["exit_code"] == 0
        assert "control_request" not in stopped
        assert [stage["status"] for stage in stopped["stages"] if stage["name"] in {"train", "generate"}] == [
            "stopped",
            "pending",
        ]
        assert no_request_files(stopped)
        # Nothing trained since the save is lost.
        progress = service.progress(run["id"])
        assert progress and progress["step"] == step and progress["unsaved_steps"] == 0

        states = [state for state in service.saved_states(run["id"]) if state["valid"]]
        assert [(state["optimizer_step"], state["resume_step"]) for state in states] == [(step, step)]
        assert states[0]["name"].endswith(f"-step{step:08d}-state")

        instant_steps(root)
        restored = service.restore_state(run["id"], states[0]["id"], step + 2, confirmed=True)
        assert "controls" not in restored and "stop_kind" not in restored
        completed = wait_for(service, restored["id"], "completed", device_released=True)
        assert completed["segment"]["step_offset"] == step
    finally:
        service.close()


def test_save_now_and_sample_now_act_while_training_continues(tmp_path: Path) -> None:
    root, job_id = job_with(
        tmp_path, steps=10, save_every=10, preview_every=10, save_state=True, sample_enabled=False, keep_checkpoints=0
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        service.control(run["id"], "save")
        saved = handled(service, run["id"], 1)
        service.control(run["id"], "sample")
        sampled = handled(service, run["id"], 2)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        assert (saved["action"], sampled["action"]) == ("save", "sample")
        assert "error" not in saved and "error" not in sampled
        assert 0 < saved["step"] <= sampled["step"] < 10
        assert no_request_files(completed)

        name = next(stage for stage in completed["stages"] if stage["name"] == "train")["args"]
        name = name[name.index("--output_name") + 1]
        directory = Path(completed["directory"])
        assert (directory / f"{name}-step{saved['step']:08d}.safetensors").is_file()
        assert (directory / "state" / f"{name}-step{saved['step']:08d}-state").is_dir()
        outputs = OutputBrowser(service).scan(run["id"])["outputs"]
        previews = sorted(
            output["metadata"]["preview"]["step"] for output in outputs if "preview" in output.get("metadata", {})
        )
        assert previews == [sampled["step"], 10]
        states = {state["optimizer_step"] for state in service.saved_states(run["id"]) if state["valid"]}
        assert saved["step"] in states
    finally:
        service.close()


def test_retention_counts_an_extra_save_like_an_interval_save(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=8, save_every=4, save_state=True, sample_enabled=False, keep_checkpoints=1)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        service.control(run["id"], "save")
        step = handled(service, run["id"], 1)["step"]
        # Requested as training started; the final step would leave the save to the train-end save.
        assert step < 8
        wait_for(service, run["id"], "completed", device_released=True)
        service.retain_checkpoints(run["id"])
        completed = service.get(run["id"])
        name = next(stage for stage in completed["stages"] if stage["name"] == "train")["args"]
        name = name[name.index("--output_name") + 1]
        # The final save plus one historical save, by step, whichever kind it is.
        saves = sorted({step, 4})
        pruned = saves[:-1]
        assert sorted(completed["retention"]["removed"]) == sorted(
            [f"{name}-step{s:08d}.safetensors" for s in pruned] + [f"{name}-step{s:08d}-state" for s in pruned]
        )
        directory = Path(completed["directory"])
        assert (directory / "state" / f"{name}-step{saves[-1]:08d}-state").is_dir()
    finally:
        service.close()


@pytest.mark.parametrize("sampling", [True, False])
@pytest.mark.parametrize("action", ["save", "save_and_stop"])
def test_request_made_after_the_final_step_is_not_applied_and_the_run_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, sampling: bool
) -> None:
    root, job_id = job_with(tmp_path, steps=2, save_every=2, save_state=False, sample_enabled=sampling)
    instant_steps(root)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")

    def request_after_training(store: Any, run: dict[str, Any], stage: dict[str, Any]) -> None:
        record_checkpoints(store, run, stage)
        # Requested after the last step, while the Run still shows training.
        run_control.request(store, run["id"], action)
        assert (Path(run["directory"]) / "logs" / REQUEST_FILE).is_file()

    monkeypatch.setattr("musubi_gui.run_execution.record_checkpoints", request_after_training)
    try:
        run = service.prepare(job_id, start=True)
        # The same outcome whether or not the Job samples after training.
        ended = wait_for(service, run["id"], "completed", device_released=True)
        assert no_request_files(ended)
        assert "stop_kind" not in ended
        assert ended["controls"][0]["error"] == TRAINING_FINISHED
        assert [stage["status"] for stage in ended["stages"] if stage["name"] == "generate"] == (
            ["completed"] if sampling else []
        )
    finally:
        service.close()


def test_alternating_wan_save_now_and_save_and_stop_give_restorable_pairs(tmp_path: Path) -> None:
    root, job_id = paired_job(tmp_path, steps=24, expert_interval=12, save_every=12)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        assert unavailable(service.get(run["id"]), "sample") == (
            "Sample now is unavailable: alternating Wan previews run as separate sampling stages."
        )
        service.control(run["id"], "save")
        saved = handled(service, run["id"], 1)
        service.control(run["id"], "save_and_stop")
        stop = handled(service, run["id"], 2)
        stopped = wait_for(service, run["id"], "stopped", device_released=True)
        assert stopped["stop_kind"] == "save"
        assert 0 < saved["step"] < stop["step"] < 12
        state_directory = Path(stopped["directory"]) / "state"
        # Named by the low expert's cumulative step, as interval saves are.
        assert sorted(path.name for path in state_directory.glob("*-low-step*-state")) == [
            f"Paired-Wan-r1-low-step{saved['step']:08d}-state",
            f"Paired-Wan-r1-low-step{stop['step']:08d}-state",
        ]
        states = {state["counts"]["low"]: state for state in service.saved_states(run["id"]) if state["valid"]}
        assert sorted(states) == [saved["step"], stop["step"]]
        assert states[stop["step"]]["counts"] == {"low": stop["step"], "high": 0}

        instant_steps(root)
        source = states[stop["step"]]
        # Until schedule step 14: the rest of the low phase, then two high-noise updates for the paired sample.
        restored = service.restore_state(run["id"], source["id"], 14, confirmed=True)
        completed = wait_for(service, restored["id"], "completed", device_released=True)
        assert (completed["sample_pair"]["low"]["steps"], completed["sample_pair"]["high"]["steps"]) == (12, 2)
    finally:
        service.close()


def test_control_endpoint_refuses_a_run_that_is_not_training(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=2, preview_every=0)
    assets = tmp_path / "assets"
    assets.mkdir()
    with TestClient(create_app(root, assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")) as client:
        run = client.post("/api/runs/prepare", json={"job_id": job_id}).json()
        deadline = time.monotonic() + 180
        while client.get(f"/api/runs/{run['id']}").json()["status"] != "prepared":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        refused = client.post(f"/api/runs/{run['id']}/control", json={"action": "save_and_stop"})
        assert refused.status_code == 409
        assert refused.json()["detail"] == "Save and stop acts on a training step; this Run isn't training."
        assert client.post(f"/api/runs/{run['id']}/control", json={"action": "explode"}).status_code == 422
        foreign = client.post(
            f"/api/runs/{run['id']}/control", json={"action": "save"}, headers={"Origin": "http://example.test"}
        )
        assert foreign.status_code == 403
        assert not (Path(client.get(f"/api/runs/{run['id']}").json()["directory"]) / "logs" / REQUEST_FILE).exists()


def test_sample_now_needs_previews() -> None:
    run = {
        "status": "training",
        "job": {"family": "wan", "config": {"recipe": "standard"}},
        "stages": [{"name": "train", "args": ["--output_name", "Job"]}],
    }
    assert unavailable(run, "sample") == "Sample now is unavailable: previews are off for this Job."
    assert unavailable(run, "save") is None
    run["stages"][0]["args"] += ["--sample_prompts", "previews.json"]
    assert unavailable(run, "sample") is None


def test_an_outcome_the_record_could_not_take_is_collected_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path)
    (tmp_path / "logs").mkdir()
    run = store.create({"id": "run", "status": "training", "directory": str(tmp_path), "stages": []})
    run = run_control.request(store, run["id"], "save")
    pending = run["control_request"]
    write_json(tmp_path / "logs" / HANDLED_FILE, {"id": pending["id"], "action": "save", "step": 3})
    original = store.update

    def unavailable_record(run_id: str, change: Any) -> dict[str, Any]:
        raise OSError("database is locked")

    monkeypatch.setattr(store, "update", unavailable_record)
    with pytest.raises(OSError):
        run_control.collect(store, run)
    monkeypatch.setattr(store, "update", original)
    # The outcome is kept for the next check, which records it and frees the controls.
    assert (tmp_path / "logs" / HANDLED_FILE).is_file()
    assert run_control.collect(store, run) == {**store.get("run")["controls"][0]}
    collected = store.get("run")
    assert "control_request" not in collected
    assert [(entry["id"], entry["step"]) for entry in collected["controls"]] == [(pending["id"], 3)]
    assert not (tmp_path / "logs" / HANDLED_FILE).exists()


def test_collecting_an_outcome_again_records_it_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = RunStore(tmp_path)
    (tmp_path / "logs").mkdir()
    run = store.create({"id": "run", "status": "training", "directory": str(tmp_path), "stages": []})
    pending = run_control.request(store, run["id"], "save")["control_request"]
    write_json(tmp_path / "logs" / HANDLED_FILE, {"id": pending["id"], "action": "save", "step": 3})
    # The handled file survives the first collection, as when removing it failed.
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)
    first = run_control.collect(store, run)
    assert run_control.collect(store, run) == first
    assert len(store.get("run")["controls"]) == 1

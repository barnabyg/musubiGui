from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import acceptance_workspace, wait_for

from musubi_gui.jobs import JobStore, as_draft
from musubi_gui.jobs_api import jobs_router
from musubi_gui.runs import RunService


def client_for(root: Path, *, fixture_mode: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(jobs_router(root, fixture_mode=fixture_mode))
    return TestClient(app)


def test_optimizer_survives_revisions_export_and_presets(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post("/api/jobs", json={"name": "8-bit", "config": {"optimizer": "AdamW8bit"}})
    assert job.status_code == 200
    saved = job.json()
    assert client_for(tmp_path).get(f"/api/jobs/{saved['id']}").json()["config"]["optimizer"] == "AdamW8bit"
    exported = client.get(f"/api/jobs/{saved['id']}/export").json()
    assert exported["draft"]["config"]["optimizer"] == "AdamW8bit"
    assert client.post("/api/jobs/import", json=exported).json()["config"]["optimizer"] == "AdamW8bit"
    preset = client.post(f"/api/jobs/{saved['id']}/presets", json={"name": "8-bit"}).json()
    target = client.post("/api/jobs", json={"name": "Default"}).json()
    assert target["config"]["optimizer"] == "AdamW"
    applied = client.post(
        f"/api/jobs/{target['id']}/apply-preset", json={"preset_id": preset["id"], "revision": target["revision"]}
    ).json()
    assert applied["config"]["optimizer"] == "AdamW8bit"


def test_unavailable_optimizer_can_be_saved_but_blocks_review(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.get("/api/jobs/training-options").json()["optimizers"] == ["AdamW"]
    job = client.post("/api/jobs", json={"config": {"optimizer": "AdamW8bit"}}).json()
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert "bitsandbytes" in next(error for error in review["errors"] if error.startswith("Optimizer:"))
    assert review["optimizer"] == "AdamW8bit"
    assert client.post("/api/jobs", json={"config": {"optimizer": "arbitrary.Module"}}).status_code == 422


def test_timestep_strategy_survives_export_and_presets(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = client.post("/api/jobs", json={"config": {"timestep_sampling": "sigmoid", "sigmoid_scale": 1.5}})
    assert response.status_code == 200
    job = response.json()
    assert client_for(tmp_path).get(f"/api/jobs/{job['id']}").json()["config"]["timestep_sampling"] == "sigmoid"
    exported = client.get(f"/api/jobs/{job['id']}/export").json()
    imported = client.post("/api/jobs/import", json=exported).json()
    assert imported["config"]["timestep_sampling"] == "sigmoid"
    assert imported["config"]["sigmoid_scale"] == 1.5
    preset = client.post(f"/api/jobs/{job['id']}/presets", json={"name": "Sigmoid"}).json()
    target = client.post("/api/jobs", json={}).json()
    assert target["config"]["timestep_sampling"] == "shift"
    applied = client.post(
        f"/api/jobs/{target['id']}/apply-preset", json={"preset_id": preset["id"], "revision": target["revision"]}
    ).json()
    assert applied["config"]["timestep_sampling"] == "sigmoid"
    assert applied["config"]["sigmoid_scale"] == 1.5


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_optimizer_reaches_training_and_restore_keeps_original_optimizer(tmp_path: Path, family: str) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    client = client_for(root, fixture_mode=True)
    assert client.get("/api/jobs/training-options").json()["optimizers"] == ["AdamW", "AdamW8bit"]
    assert "simulated" in client.get("/api/jobs/training-options").json()["reason"]
    store = JobStore(root)
    job = store.get(next(job["id"] for job in fixture["jobs"] if job["family"] == family))
    draft = as_draft(job)
    draft.config.optimizer = "AdamW8bit"
    if family == "wan":
        draft.config.timestep_sampling = "sigmoid"
        draft.config.sigmoid_scale = 1.5
    draft.config.save_state = True
    saved = store.save(job.id, draft, job.revision)
    review = client.post(f"/api/jobs/{saved.id}/review", json={}).json()
    assert review["valid"], review["errors"]
    args = next(stage["args"] for stage in review["stages"] if stage["name"] == "train")
    assert args[args.index("--optimizer_type") + 1] == "AdamW8bit"
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(saved.id)
        wait_for(service, run["id"], "prepared")
        service.start(run["id"])
        wait_for(service, run["id"], "completed", device_released=True)
        draft.config.optimizer = "AdamW"
        draft.config.timestep_sampling = "uniform"
        store.save(job.id, draft, saved.revision)
        states = service.saved_states(run["id"])
        service.store.update(run["id"], lambda record: record["job"]["config"].update(optimizer="AdamW"))
        with pytest.raises(ValueError, match="Optimizer differs"):
            service.restore_state(run["id"], next(state["id"] for state in states if state["valid"]), 1, confirmed=True)
        service.store.update(run["id"], lambda record: record["job"]["config"].update(optimizer="AdamW8bit"))
        if family == "wan":
            service.store.update(run["id"], lambda record: record["job"]["config"].update(timestep_sampling="uniform"))
            with pytest.raises(ValueError, match="Timestep strategy differs"):
                service.restore_state(
                    run["id"], next(state["id"] for state in states if state["valid"]), 1, confirmed=True
                )
            service.store.update(run["id"], lambda record: record["job"]["config"].update(timestep_sampling="sigmoid"))
        restored = service.restore_state(
            run["id"], next(state["id"] for state in states if state["valid"]), 1, confirmed=True
        )
        assert restored["job"]["config"]["optimizer"] == "AdamW8bit"
        assert restored["job"]["config"]["timestep_sampling"] == ("sigmoid" if family == "wan" else "uniform")
        train = next(stage["args"] for stage in restored["stages"] if stage["name"] == "train")
        assert train[train.index("--optimizer_type") + 1] == "AdamW8bit"
        wait_for(service, restored["id"], "completed", device_released=True)
    finally:
        service.close()

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.acceptance import prepare
from musubi_gui.jobs_api import jobs_router
from musubi_gui.run_store import RunStore
from musubi_gui.runs import RunService
from musubi_gui.runs_api import runs_router


def client_for(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(jobs_router(root))
    return TestClient(app)


def add_run(root: Path, job_id: str, status: str, **details: object) -> dict:
    return RunStore(root).create(
        {"id": f"run-{status}", "job": {"id": job_id}, "status": status, "fingerprint": status, **details}
    )


def new_job(client: TestClient, name: str = "tune-A1") -> dict:
    return client.post("/api/jobs", json={"name": name, "family": "wan"}).json()


def test_an_archived_job_leaves_the_list_and_keeps_its_revisions_byte_for_byte(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job, kept = new_job(client), new_job(client, "keeper")
    revision = client.put(f"/api/jobs/{job['id']}", json={"name": "second", "family": "wan", "revision": 1}).json()
    directory = tmp_path / "jobs" / job["id"]
    before = {file.name: file.read_bytes() for file in directory.iterdir()}

    assert client.post(f"/api/jobs/{job['id']}/archive", json={}).status_code == 200

    assert [item["id"] for item in client.get("/api/jobs").json()] == [kept["id"]]
    assert [item["id"] for item in client.get("/api/jobs/archived").json()] == [job["id"]]
    assert {file.name: file.read_bytes() for file in directory.iterdir()} == before
    assert client.get(f"/api/jobs/{job['id']}/revisions/1").json()["name"] == "tune-A1"
    assert client.get(f"/api/jobs/{job['id']}").json()["revision"] == revision["revision"]
    # Still archived after a restart.
    assert [item["id"] for item in client_for(tmp_path).get("/api/jobs").json()] == [kept["id"]]


def test_a_restored_job_returns_to_the_list_and_can_be_edited(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = new_job(client)
    client.post(f"/api/jobs/{job['id']}/archive", json={})
    assert client.post(f"/api/jobs/{job['id']}/restore", json={}).status_code == 200
    assert [item["id"] for item in client.get("/api/jobs").json()] == [job["id"]]
    assert client.get("/api/jobs/archived").json() == []
    edited = client.put(f"/api/jobs/{job['id']}", json={"name": "back", "family": "wan", "revision": 1})
    assert edited.status_code == 200


@pytest.mark.parametrize(
    "status",
    [
        "pending_preparation",
        "preparing",
        "pending_training",
        "training",
        "generating",
        "stopping",
        "orphaned",
        "uncertain",
    ],
)
def test_a_job_with_an_active_run_cannot_be_archived(tmp_path: Path, status: str) -> None:
    client = client_for(tmp_path)
    job = new_job(client)
    add_run(tmp_path, job["id"], status)
    refused = client.post(f"/api/jobs/{job['id']}/archive", json={})
    assert refused.status_code == 409
    assert "active" in refused.json()["detail"]
    assert [item["id"] for item in client.get("/api/jobs").json()] == [job["id"]]


def test_an_ended_run_with_processes_to_reconcile_still_blocks_archiving(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = new_job(client)
    add_run(tmp_path, job["id"], "interrupted", processes=[{"pid": 1}])
    assert client.post(f"/api/jobs/{job['id']}/archive", json={}).status_code == 409


@pytest.mark.parametrize("status", ["prepared", "completed", "failed", "cancelled", "stopped", "interrupted"])
def test_a_job_whose_runs_are_not_active_can_be_archived_and_its_runs_stay(tmp_path: Path, status: str) -> None:
    client = client_for(tmp_path)
    job = new_job(client)
    run = add_run(tmp_path, job["id"], status)
    assert client.post(f"/api/jobs/{job['id']}/archive", json={}).status_code == 200
    assert RunStore(tmp_path).get(run["id"])["status"] == status


def test_another_jobs_active_run_does_not_block_archiving(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job, other = new_job(client), new_job(client, "other")
    add_run(tmp_path, other["id"], "training")
    assert client.post(f"/api/jobs/{job['id']}/archive", json={}).status_code == 200


def test_an_archived_job_cannot_be_changed_or_used_as_a_preset_source(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = new_job(client)
    client.post(f"/api/jobs/{job['id']}/archive", json={})
    route = f"/api/jobs/{job['id']}"
    assert client.put(route, json={"name": "edit", "family": "wan", "revision": 1}).status_code == 422
    assert client.post(f"{route}/presets", json={"name": "preset"}).status_code == 422
    assert client.post(f"{route}/convert", json={"family": "h3", "revision": 1}).status_code == 422
    selection = {"preset_id": "measured-wan-832x480x33-r16", "revision": 1}
    assert client.post(f"{route}/apply-preset", json=selection).status_code == 422
    assert [preset["family"] for preset in client.get("/api/jobs/presets").json()] == ["wan", "h3"]
    assert sorted(file.name for file in (tmp_path / "jobs" / job["id"]).iterdir()) == ["1.json"]


def test_archiving_or_restoring_a_missing_job_is_not_found(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.post(f"/api/jobs/{'a' * 32}/archive", json={}).status_code == 404
    assert client.post(f"/api/jobs/{'a' * 32}/restore", json={}).status_code == 404


def test_prepare_refuses_an_archived_job_and_its_runs_stay_browsable(tmp_path: Path) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    root = Path(fixture["state"])
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False)
    try:
        app = FastAPI()
        app.include_router(jobs_router(root, fixture_mode=True))
        app.include_router(runs_router(service))
        client = TestClient(app)
        first, second = fixture["jobs"][0]["id"], fixture["jobs"][1]["id"]
        run = service.prepare(first)
        assert client.post(f"/api/jobs/{first}/archive", json={}).status_code == 409
        service.cancel(run["id"])
        assert client.post(f"/api/jobs/{first}/archive", json={}).status_code == 200
        assert first not in [job["id"] for job in client.get("/api/jobs").json()]
        browsed = client.get(f"/api/runs/{run['id']}").json()
        assert browsed["job"]["id"] == first
        assert run["id"] in [item["id"] for item in client.get("/api/runs").json()]
        assert client.get(f"/api/runs/{run['id']}/logs").status_code == 200

        assert client.post(f"/api/jobs/{second}/archive", json={}).status_code == 200
        refused = client.post("/api/runs/prepare", json={"job_id": second})
        assert refused.status_code == 409
        assert "archived" in refused.json()["detail"]
    finally:
        service.close()

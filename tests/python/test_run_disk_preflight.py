from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.acceptance import prepare
from musubi_gui.disk_estimate import FREE_SPACE_OVERRIDE
from musubi_gui.jobs import jobs_router
from musubi_gui.runs import RunService
from musubi_gui.runs_api import runs_router


@pytest.fixture
def fixture(tmp_path: Path) -> dict:
    return prepare(tmp_path / "acceptance", "fixture", None)


@pytest.fixture
def service(fixture: dict, tmp_path: Path):
    running = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    yield running
    running.close()


def client_for(service: RunService, fixture: dict) -> TestClient:
    app = FastAPI()
    app.include_router(jobs_router(Path(fixture["state"]), fixture_mode=True))
    app.include_router(runs_router(service))
    return TestClient(app)


def test_review_reports_the_estimate_and_each_target_volume(service: RunService, fixture: dict) -> None:
    client = client_for(service, fixture)
    disk = client.post(f"/api/jobs/{fixture['jobs'][0]['id']}/review", json={}).json()["disk"]
    assert {"media_snapshot", "caches", "checkpoints", "states", "samples", "saves"} <= set(disk["estimate"])
    assert disk["estimate"]["media_snapshot"] > 0
    assert disk["volumes"][0]["shortfall"] == 0
    assert disk["volumes"][0]["free"] > 0
    assert set(disk["volumes"][0]["roots"]) == {"Output root", "Cache root"}


def test_low_disk_warns_in_review_without_making_the_job_invalid(
    service: RunService, fixture: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FREE_SPACE_OVERRIDE, "1000")
    review = client_for(service, fixture).post(f"/api/jobs/{fixture['jobs'][0]['id']}/review", json={}).json()
    assert review["valid"] is True
    assert review["disk"]["volumes"][0]["shortfall"] > 0
    assert any("Low disk space" in warning and "short" in warning for warning in review["warnings"])


def test_prepare_goes_ahead_on_low_disk(service: RunService, fixture: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FREE_SPACE_OVERRIDE, "1000")
    client = client_for(service, fixture)
    prepared = client.post("/api/runs/prepare", json={"job_id": fixture["jobs"][0]["id"]})
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "pending_preparation"


def test_the_free_space_override_is_ignored_outside_fixture_mode(
    fixture: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FREE_SPACE_OVERRIDE, "1000")
    app = FastAPI()
    app.include_router(jobs_router(Path(fixture["state"])))
    disk = TestClient(app).post(f"/api/jobs/{fixture['jobs'][0]['id']}/review", json={}).json()["disk"]
    assert disk["volumes"][0]["free"] != 1000

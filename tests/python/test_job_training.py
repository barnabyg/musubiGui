"""The Job page's current training: its latest segment, and the saved state Start resumes."""

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from helpers import instant_steps
from test_run_control import job_with
from test_run_resume_revision import edit

from musubi_gui.app import create_app


def settled(client: TestClient, run_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while (run := client.get(f"/api/runs/{run_id}").json())["status"] != status:
        assert run["status"] != "failed", run
        assert time.monotonic() < deadline, run
        time.sleep(0.05)
    return dict(run)


def test_the_job_training_follows_the_latest_segment_and_names_its_save(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=3, save_every=1, save_state=True, preview_every=0)
    instant_steps(root)
    assets = tmp_path / "assets"
    assets.mkdir()
    with TestClient(create_app(root, assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")) as client:
        empty = client.get(f"/api/jobs/{job_id}/training").json()
        assert (empty["run_id"], empty["resume"]) == (None, None)
        assert client.get(f"/api/jobs/{uuid4().hex}/training").status_code == 404

        run = client.post("/api/runs/prepare", json={"job_id": job_id, "start": True}).json()
        settled(client, run["id"], "completed")
        # Read without inspecting states: the save is named by its folder, the final state by the target.
        training = client.get(f"/api/jobs/{job_id}/training").json()
        assert (training["run_id"], training["chain_id"], training["status"]) == (run["id"], run["id"], "completed")
        assert training["resume"] == {"run_id": run["id"], "run_status": "completed", "saved_step": 3}
        assert "saved_states" not in client.get(f"/api/runs/{run['id']}").json()

        revision = edit(root, job_id, steps=5)
        segment = client.post(f"/api/runs/{run['id']}/resume", json={"job_revision": revision}).json()
        # A training that is still running has nothing to resume.
        training = client.get(f"/api/jobs/{job_id}/training").json()
        assert (training["run_id"], training["chain_id"]) == (segment["id"], run["id"])
        settled(client, segment["id"], "completed")
        training = client.get(f"/api/jobs/{job_id}/training").json()
        assert training["status"] == "completed"
        assert training["resume"] == {"run_id": segment["id"], "run_status": "completed", "saved_step": 5}
        review = client.post(f"/api/jobs/{job_id}/review", json={}).json()["resume_compatibility"]
        assert review["run_id"] == segment["id"]

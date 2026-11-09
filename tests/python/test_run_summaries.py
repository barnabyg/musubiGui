from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_runs import plan

from musubi_gui.runs import RunService
from musubi_gui.runs_api import runs_router


def test_summary_history_keeps_details_compatible_and_changes_with_run_state(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite", autostart=False)
    try:
        execution = plan(tmp_path)
        execution.job["config"] = {"sample_prompt": "Frozen settings belong in detail, not history."}
        run = service.submit(execution)
        app = FastAPI()
        app.include_router(runs_router(service))
        client = TestClient(app)

        response = client.get("/api/runs/summaries")
        assert response.status_code == 200
        summary = response.json()[0]
        assert summary["id"] == run["id"]
        assert summary["job"] == {"id": "one", "name": "one", "family": "wan", "revision": 1}
        assert summary["status"] == "pending_preparation"
        assert summary["stages"] == [{"name": "latent_cache", "status": "pending"}]
        assert not {"manifest", "settings", "input_files", "provenance", "directory"} & summary.keys()
        assert "args" not in summary["stages"][0]
        assert client.get("/api/runs").json()[0] == run
        assert client.get(f"/api/runs/{run['id']}").json() == run

        service.store.update(run["id"], lambda record: record.update(status="cancelled"))
        changed = client.get("/api/runs/summaries").json()[0]
        assert changed["status"] == "cancelled"
        assert changed["change_key"] != summary["change_key"]
        assert client.get("/api/runs/summaries").json()[0]["change_key"] == changed["change_key"]
        service.store.update(run["id"], lambda record: record.update(error="A recovered diagnostic changed."))
        assert client.get("/api/runs/summaries").json()[0]["change_key"] != changed["change_key"]
    finally:
        service.close()

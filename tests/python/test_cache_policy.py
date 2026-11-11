from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import acceptance_workspace, wait_for

from musubi_gui.datasets_api import dataset_router
from musubi_gui.jobs_api import jobs_router
from musubi_gui.runs import RunService
from musubi_gui.snapshots import digest_file


@pytest.mark.parametrize("family", ["wan", "h3"])
@pytest.mark.parametrize("policy", ["latent_cache_policy", "text_cache_policy"])
def test_rebuild_policy_runs_one_encoder_without_mutating_other_runs(tmp_path: Path, family: str, policy: str) -> None:
    fixture = acceptance_workspace(tmp_path)
    state = Path(fixture["state"])
    app = FastAPI()
    app.include_router(jobs_router(state, fixture_mode=True))
    client = TestClient(app)
    service = RunService(state, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        job_id = next(job["id"] for job in fixture["jobs"] if job["family"] == family)
        first = wait_for(service, service.prepare(job_id)["id"], "prepared", device_released=True)
        hashes = {path: digest_file(path) for path in Path(first["cache"]).rglob("*.safetensors")}
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["config"]["latent_cache_policy"] == job["config"]["text_cache_policy"] == "reuse"
        job["config"][policy] = "rebuild"
        saved = client.put(
            f"/api/jobs/{job_id}",
            json={key: value for key, value in job.items() if key not in {"id", "version"}},
        )
        assert saved.status_code == 200
        second = wait_for(service, service.prepare(job_id)["id"], "prepared", device_released=True)
        assert second["stage_keys"] == first["stage_keys"]
        stages = {stage["name"]: stage for stage in second["stages"]}
        assert stages[policy.removesuffix("_policy")]["cache_reason"] == "Rebuild requested by this Run."
        assert stages["latent_cache"]["status"] == ("completed" if policy == "latent_cache_policy" else "reused")
        assert stages["text_cache"]["status"] == ("completed" if policy == "text_cache_policy" else "reused")
        assert {path: digest_file(path) for path in hashes} == hashes
        assert Path(second["cache"]) != Path(first["cache"])
        exported = client.get(f"/api/jobs/{job_id}/export").json()
        imported = client.post("/api/jobs/import", json=exported).json()
        assert imported["config"][policy] == "rebuild"
        preset = client.post(f"/api/jobs/{job_id}/presets", json={"name": "Cache policy"}).json()
        applied = client.post(
            f"/api/jobs/{imported['id']}/apply-preset",
            json={"preset_id": preset["id"], "revision": imported["revision"]},
        ).json()
        assert applied["config"][policy] == "rebuild"
        review = client.post(f"/api/jobs/{job_id}/review", json={}).json()
        assert review["cache_policy"][policy.removesuffix("_policy")] == "rebuild"
    finally:
        service.close()


def test_reuse_rebuilds_stale_caption_and_resolution_inputs(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    state = Path(fixture["state"])
    app = FastAPI()
    app.include_router(jobs_router(state, fixture_mode=True))
    app.include_router(dataset_router(state))
    client = TestClient(app)
    service = RunService(state, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        fixture_job = fixture["jobs"][0]
        job_id = fixture_job["id"]
        first = wait_for(service, service.prepare(job_id)["id"], "prepared", device_released=True)
        dataset_id = fixture_job["dataset_id"]
        item = client.get(f"/api/datasets/{dataset_id}/items").json()[0]
        caption_url = f"/api/datasets/{dataset_id}/items/{item['id']}/caption"
        caption = client.get(caption_url).json()
        result = client.put(caption_url, json={"revision": caption["revision"], "text": "A different caption."})
        assert result.status_code == 200
        caption_run = wait_for(service, service.prepare(job_id)["id"], "prepared", device_released=True)
        assert caption_run["stage_keys"]["latent_cache"] == first["stage_keys"]["latent_cache"]
        assert caption_run["stage_keys"]["text_cache"] != first["stage_keys"]["text_cache"]
        assert [stage["status"] for stage in caption_run["stages"][:2]] == ["reused", "completed"]
        job = client.get(f"/api/jobs/{job_id}").json()
        job["config"]["width"] = 320
        assert (
            client.put(
                f"/api/jobs/{job_id}",
                json={key: value for key, value in job.items() if key not in {"id", "version"}},
            ).status_code
            == 200
        )
        resized = wait_for(service, service.prepare(job_id)["id"], "prepared", device_released=True)
        assert resized["stage_keys"] != caption_run["stage_keys"]
        assert [stage["status"] for stage in resized["stages"][:2]] == ["completed", "completed"]
    finally:
        service.close()

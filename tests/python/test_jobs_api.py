from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.jobs import jobs_router
from musubi_gui.settings import Settings, save_settings


def client_for(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(jobs_router(root))
    return TestClient(app)


def test_incomplete_wan_draft_survives_restart_without_losing_controls(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = client.post(
        "/api/jobs",
        json={"name": "日本語 draft", "family": "wan", "config": {"rank": 16, "learning_rate": 0.0002}},
    )
    assert response.status_code == 200
    job = response.json()
    reopened = client_for(tmp_path).get(f"/api/jobs/{job['id']}").json()
    assert reopened["name"] == "日本語 draft"
    assert reopened["config"]["rank"] == 16
    assert reopened["config"]["learning_rate"] == 0.0002
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert review["valid"] is False
    assert any("dataset" in error.lower() for error in review["errors"])


def test_concurrent_job_edits_preserve_the_newer_revision(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post("/api/jobs", json={"name": "Original", "family": "wan"}).json()
    update = {"name": "Newer", "family": "wan", "revision": job["revision"]}
    assert client.put(f"/api/jobs/{job['id']}", json=update).status_code == 200
    assert client.put(f"/api/jobs/{job['id']}", json={**update, "name": "Stale"}).status_code == 409
    assert client.get(f"/api/jobs/{job['id']}").json()["name"] == "Newer"
    assert client.get(f"/api/jobs/{job['id']}/revisions/1").json()["name"] == "Original"


def test_wan_review_matches_saved_training_and_preparation_controls(tmp_path: Path) -> None:
    settings = Settings(
        checkout=str(tmp_path / "upstream"),
        python=str(tmp_path / "python.exe"),
        output_root=str(tmp_path / "output"),
        cache_root=str(tmp_path / "cache"),
        model_roots=[str(tmp_path / "models")],
        models={
            name: str(tmp_path / "models" / f"{name}.pth") for name in ["wan_low", "wan_high", "wan_vae", "wan_t5"]
        },
    )
    save_settings(tmp_path, settings)
    client = client_for(tmp_path)
    job = client.post(
        "/api/jobs",
        json={
            "name": "Wide video",
            "family": "wan",
            "config": {
                "width": 320,
                "height": 256,
                "frames": 33,
                "rank": 16,
                "repeats": 3,
                "steps": 10,
                "save_state": True,
            },
        },
    ).json()
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert "resolution = [320, 256]" in review["dataset_toml"]
    assert "num_repeats = 3" in review["dataset_toml"]
    assert "target_frames = [33]" in review["dataset_toml"]
    train = next(stage["args"] for stage in review["stages"] if stage["name"] == "train")
    assert train[train.index("--network_dim") + 1] == "16"
    assert train[train.index("--max_train_steps") + 1] == "10"
    assert "--save_state_on_train_end" in train
    sample = next(stage["args"] for stage in review["stages"] if stage["name"] == "generate")
    assert sample[sample.index("--video_size") + 1 : sample.index("--video_size") + 3] == ["256", "320"]


def test_incompatible_wan_options_remain_editable_but_review_blocks_execution(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post(
        "/api/jobs",
        json={"name": "Fix memory", "family": "wan", "config": {"offload_inactive_dit": True, "frames": 18}},
    ).json()
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert review["valid"] is False
    assert any("offload" in error for error in review["errors"])
    assert any("4*n+1" in error for error in review["errors"])

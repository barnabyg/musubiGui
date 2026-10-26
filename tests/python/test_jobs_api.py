import json
from pathlib import Path

import pytest
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


def test_h3_draft_defaults_and_native_stages_match_guidance_loss_recipe(tmp_path: Path) -> None:
    save_settings(
        tmp_path,
        Settings(
            models={
                name: str(tmp_path / f"{name}.safetensors")
                for name in ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]
            }
        ),
    )
    client = client_for(tmp_path)
    response = client.post("/api/jobs", json={"name": "Joint video/audio", "family": "h3"})
    assert response.status_code == 200
    job = response.json()
    assert job["config"]["frames"] == 124
    assert job["config"]["blocks_to_swap"] == 48
    assert job["config"]["rank"] == 4
    reopened = client_for(tmp_path).get(f"/api/jobs/{job['id']}").json()
    assert reopened == job
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    for stage in review["stages"]:
        args = stage["args"]
        assert args[args.index("--task") + 1] == "t2va"
    train = next(stage["args"] for stage in review["stages"] if stage["name"] == "train")
    assert "--block_swap_h2d_only" in train
    assert train[train.index("--h3_guidance_loss_scale") + 1] == "4"
    text = next(stage["args"] for stage in review["stages"] if stage["name"] == "text_cache")
    assert "--uncond_output" in text
    assert "target_frames = [124]" in review["dataset_toml"]


def test_h3_invalid_geometry_and_memory_are_saved_but_not_executable(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post(
        "/api/jobs",
        json={
            "name": "Repair H3",
            "family": "h3",
            "config": {"width": 272, "frames": 125, "fp8_scaled": True, "block_swap_h2d_only": False},
        },
    ).json()
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert review["valid"] is False
    assert any("32" in error for error in review["errors"])
    assert any("17*n+5" in error for error in review["errors"])
    assert any("FP8" in error for error in review["errors"])
    assert any("H2D" in error for error in review["errors"])


def test_duplicate_presets_and_model_conversion_preserve_original_revisions(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    original = client.post("/api/jobs", json={"name": "Original", "family": "wan", "config": {"rank": 16}}).json()
    copied_response = client.post(f"/api/jobs/{original['id']}/duplicate", json={})
    assert copied_response.status_code == 200
    copied = copied_response.json()
    assert copied["id"] != original["id"]
    assert copied["config"]["rank"] == 16
    original_review = client.post(f"/api/jobs/{original['id']}/review", json={}).json()
    copied_review = client.post(f"/api/jobs/{copied['id']}/review", json={}).json()
    assert original_review["paths"]["output"] != copied_review["paths"]["output"]
    preset = client.post(f"/api/jobs/{original['id']}/presets", json={"name": "Rank sixteen"}).json()
    other = client.post("/api/jobs", json={"name": "Other", "family": "wan"}).json()
    applied = client.post(
        f"/api/jobs/{other['id']}/apply-preset", json={"preset_id": preset["id"], "revision": 1}
    ).json()
    assert applied["config"]["rank"] == 16
    assert applied["name"] == "Other"
    preview = client.post(f"/api/jobs/{other['id']}/convert", json={"family": "h3", "revision": 2}).json()
    assert "frames" in [change["field"] for change in preview["changes"]]
    assert client.get(f"/api/jobs/{other['id']}").json()["family"] == "wan"
    converted = client.post(
        f"/api/jobs/{other['id']}/convert", json={"family": "h3", "revision": 2, "confirmed": True}
    ).json()
    assert converted["family"] == "h3"
    assert converted["config"]["frames"] == 124
    assert client.get(f"/api/jobs/{other['id']}/revisions/2").json()["family"] == "wan"
    assert client.get(f"/api/jobs/{original['id']}").json() == original


def test_versioned_export_roundtrip_and_unknown_fields_are_not_silently_discarded(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post(
        "/api/jobs",
        json={
            "name": "Round trip",
            "family": "h3",
            "config": {"seed": 789, "sample_prompt": "雪と音\nsecond line", "audio_policy": "allow-missing"},
        },
    ).json()
    exported_response = client.get(f"/api/jobs/{job['id']}/export")
    assert exported_response.status_code == 200
    exported = exported_response.json()
    imported = client.post("/api/jobs/import", json=exported)
    assert imported.status_code == 200
    assert imported.json()["config"] == job["config"]
    assert imported.json()["id"] != job["id"]
    future = {**exported, "version": 99}
    assert client.post("/api/jobs/import", json=future).status_code == 422
    unsupported = {**exported, "draft": {**exported["draft"], "unknown_switch": True}}
    assert client.post("/api/jobs/import", json=unsupported).status_code == 422
    older = {**exported, "draft": {**exported["draft"], "config": {"rank": 4, "frames": 124}}}
    restored = client.post("/api/jobs/import", json=older)
    assert restored.status_code == 200
    assert restored.json()["config"]["block_swap_h2d_only"] is True


def test_export_redacts_recognised_credentials_and_requires_a_known_format(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    job = client.post("/api/jobs", json={"name": "Redaction", "config": {"sample_prompt": "hf_" + "a" * 24}}).json()
    exported = client.get(f"/api/jobs/{job['id']}/export").json()
    assert exported["draft"]["config"]["sample_prompt"] == "[REDACTED]"
    assert client.post("/api/jobs/import", json={"draft": exported["draft"]}).status_code == 422
    assert client.post("/api/jobs/import", json={**exported, "format": "other-trainer"}).status_code == 422


def test_model_review_discloses_lossy_weight_conversion_provenance(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    header = json.dumps(
        {"__metadata__": {"source_precision": "LOSSY FP8; dequantization does not recover original full precision"}}
    ).encode()
    weight = models / "low.safetensors"
    weight.write_bytes(len(header).to_bytes(8, "little") + header)
    save_settings(tmp_path, Settings(model_roots=[str(models)], models={"wan_low": str(weight)}))
    client = client_for(tmp_path)
    job = client.post("/api/jobs", json={"name": "Converted weights"}).json()
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert "LOSSY FP8" in review["model_provenance"]["wan_low"]["source_precision"]
    assert any("full precision" in warning for warning in review["warnings"])


def test_unpublished_revision_tempfiles_do_not_hide_or_replace_published_jobs(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    original = client.post("/api/jobs", json={"name": "Published job"}).json()
    pending = tmp_path / "jobs" / original["id"] / ".pending-interrupted.json"
    pending.write_text('{"incomplete":', encoding="utf-8")
    assert client.get(f"/api/jobs/{original['id']}").json() == original
    assert client.get("/api/jobs").json() == [original]
    saved = client.put(f"/api/jobs/{original['id']}", json={"name": "Next published revision", "revision": 1})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    assert client.get(f"/api/jobs/{original['id']}/revisions/1").json() == original
    assert pending.read_text(encoding="utf-8") == '{"incomplete":'


def test_unpublished_job_directories_do_not_hide_the_published_library(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    original = client.post("/api/jobs", json={"name": "Existing job"}).json()
    empty = tmp_path / "jobs" / ("a" * 32)
    empty.mkdir()
    staging = tmp_path / "jobs" / ("b" * 32)
    staging.mkdir()
    pending = staging / ".pending-first-revision.json"
    pending.write_bytes(b"unfinished first revision")
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == [original]
    assert client.get(f"/api/jobs/{empty.name}").status_code == 404
    assert client.get(f"/api/jobs/{staging.name}").status_code == 404
    assert list(empty.iterdir()) == []
    assert pending.read_bytes() == b"unfinished first revision"


@pytest.mark.parametrize("filename", ["future.json", "unknown.txt", ".pending-unknown.txt"])
@pytest.mark.parametrize("published", [True, False])
def test_unknown_job_storage_remains_blocked_and_preserved(tmp_path: Path, filename: str, published: bool) -> None:
    client = client_for(tmp_path)
    original = client.post("/api/jobs", json={"name": "Keep this revision"}).json()
    job_id = original["id"] if published else "c" * 32
    directory = tmp_path / "jobs" / job_id
    directory.mkdir(exist_ok=True)
    unknown = directory / filename
    unknown.write_bytes(b'{"version":99,"unknown":"preserve me"}')
    assert client.get(f"/api/jobs/{job_id}").status_code == 422
    assert client.get("/api/jobs").status_code == 422
    assert unknown.read_bytes() == b'{"version":99,"unknown":"preserve me"}'
    assert json.loads((tmp_path / "jobs" / original["id"] / "1.json").read_text(encoding="utf-8")) == original

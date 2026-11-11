"""Dataset geometry survives the public job workflow and reaches preparation."""

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import acceptance_workspace, wait_for

from musubi_gui.app import create_app
from musubi_gui.jobs import smoke_config
from musubi_gui.runs import RunService


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_dataset_resolutions_survive_export_and_reach_review_without_changing_samples(
    tmp_path: Path, family: str
) -> None:
    workspace = acceptance_workspace(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    client = TestClient(
        create_app(Path(workspace["state"]), assets, fixture_mode=True, device_path=tmp_path / "gpu.db")
    )
    ids = [entry["dataset_id"] for entry in workspace["jobs"] if entry["name"].endswith("1")]
    datasets = [
        {"dataset_id": ids[0], "resolution": [320, 256]},
        {"dataset_id": ids[1], "resolution": [256, 320]},
    ]
    response = client.post(
        "/api/jobs",
        json={"name": "Two sizes", "family": family, "datasets": datasets, "config": smoke_config(family).model_dump()},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    reopened = client.get(f"/api/jobs/{job['id']}").json()
    assert [entry["resolution"] for entry in reopened["datasets"]] == [[320, 256], [256, 320]]
    exported = client.get(f"/api/jobs/{job['id']}/export").json()
    imported = client.post("/api/jobs/import", json=exported)
    assert imported.status_code == 200, imported.text
    assert imported.json()["datasets"] == job["datasets"]
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    native = tomllib.loads(review["dataset_toml"])
    assert [section["resolution"] for section in native["datasets"]] == [[320, 256], [256, 320]]
    assert [section["resolution"] for section in review["sections"]] == [[320, 256], [256, 320]]
    generate = next(stage["args"] for stage in review["stages"] if stage["name"] == "generate")
    offset = generate.index("--video_size")
    assert generate[offset + 1 : offset + 3] == ["256", "256"]


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_invalid_dataset_resolution_is_a_saveable_draft_but_review_blocks_it(tmp_path: Path, family: str) -> None:
    workspace = acceptance_workspace(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    client = TestClient(
        create_app(Path(workspace["state"]), assets, fixture_mode=True, device_path=tmp_path / "gpu.db")
    )
    dataset = workspace["jobs"][0]["dataset_id"]
    response = client.post(
        "/api/jobs",
        json={"name": "Repair size", "family": family, "datasets": [{"dataset_id": dataset, "resolution": [65, 256]}]},
    )
    assert response.status_code == 200, response.text
    review = client.post(f"/api/jobs/{response.json()['id']}/review", json={}).json()
    errors: list[str] = review["errors"]
    index = next(index for index, error in enumerate(errors) if "resolution" in error.lower())
    assert review["valid"] is False
    assert review["error_targets"][str(index)] == {"field": f"resolution-{dataset}-width"}


def test_presets_restore_matching_dataset_resolution_including_inheritance(tmp_path: Path) -> None:
    workspace = acceptance_workspace(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    client = TestClient(
        create_app(Path(workspace["state"]), assets, fixture_mode=True, device_path=tmp_path / "gpu.db")
    )
    dataset = workspace["jobs"][0]["dataset_id"]
    original = client.post(
        "/api/jobs", json={"name": "Inherited", "family": "h3", "datasets": [{"dataset_id": dataset}]}
    ).json()
    inherited_preset = client.post(f"/api/jobs/{original['id']}/presets", json={"name": "Inherited size"}).json()
    changed = client.put(
        f"/api/jobs/{original['id']}",
        json={
            "name": original["name"],
            "family": "h3",
            "revision": 1,
            "datasets": [{"dataset_id": dataset, "resolution": [320, 256]}],
            "config": original["config"],
        },
    ).json()
    explicit_preset = client.post(f"/api/jobs/{original['id']}/presets", json={"name": "Explicit size"}).json()
    inherited = client.post(
        f"/api/jobs/{original['id']}/apply-preset",
        json={"preset_id": inherited_preset["id"], "revision": changed["revision"]},
    )
    assert inherited.status_code == 200, inherited.text
    assert "resolution" not in inherited.json()["datasets"][0]
    explicit = client.post(
        f"/api/jobs/{original['id']}/apply-preset",
        json={"preset_id": explicit_preset["id"], "revision": inherited.json()["revision"]},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["datasets"][0]["resolution"] == [320, 256]


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_single_dataset_override_changes_prepared_cache_identity_and_disk_only_for_training(
    tmp_path: Path, family: str
) -> None:
    workspace = acceptance_workspace(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    state = Path(workspace["state"])
    client = TestClient(create_app(state, assets, fixture_mode=True, device_path=tmp_path / "gpu.db"))
    dataset = next(entry["dataset_id"] for entry in workspace["jobs"] if entry["family"] == family)
    job = client.post(
        "/api/jobs",
        json={
            "name": "One size",
            "family": family,
            "datasets": [{"dataset_id": dataset}],
            "config": smoke_config(family).model_dump(),
        },
    ).json()
    service = RunService(state, fixture_mode=True, device_path=tmp_path / "other.db", autostart=False)
    try:
        first = service.prepare(job["id"])
        first_review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
        changed = client.put(
            f"/api/jobs/{job['id']}",
            json={
                "name": job["name"],
                "family": family,
                "revision": job["revision"],
                "datasets": [{"dataset_id": dataset, "resolution": [512, 256]}],
                "config": job["config"],
            },
        )
        assert changed.status_code == 200, changed.text
        second = service.prepare(job["id"])
        second_review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
        assert first["stage_keys"]["latent_cache"] != second["stage_keys"]["latent_cache"]
        assert first["stage_keys"]["text_cache"] != second["stage_keys"]["text_cache"]
        assert second_review["disk"]["estimate"]["caches"] > first_review["disk"]["estimate"]["caches"]
        assert second_review["disk"]["estimate"]["samples"] == first_review["disk"]["estimate"]["samples"]
        native = tomllib.loads((Path(second["directory"]) / "dataset.toml").read_text(encoding="utf-8"))
        assert native["datasets"][0]["resolution"] == [512, 256]
        assert native["general"]["resolution"] == [256, 256]
    finally:
        service.close()


def test_two_dataset_resolutions_reuse_compatible_caches_and_rebuild_after_one_size_changes(tmp_path: Path) -> None:
    workspace = acceptance_workspace(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    state = Path(workspace["state"])
    client = TestClient(create_app(state, assets, fixture_mode=True, device_path=tmp_path / "api-gpu.db"))
    ids = [
        next(entry["dataset_id"] for entry in workspace["jobs"] if entry["family"] == family)
        for family in ("wan", "h3")
    ]
    config = smoke_config("wan").model_copy(update={"sample_enabled": False}).model_dump()
    created = client.post(
        "/api/jobs",
        json={
            "name": "Two cached sizes",
            "family": "wan",
            "config": config,
            "datasets": [
                {"dataset_id": ids[0], "resolution": [320, 256]},
                {"dataset_id": ids[1], "resolution": [256, 320]},
            ],
        },
    )
    assert created.status_code == 200, created.text
    job = created.json()
    service = RunService(state, fixture_mode=True, device_path=tmp_path / "worker-gpu.db")
    try:
        first = service.prepare(job["id"])
        wait_for(service, first["id"], "prepared", device_released=True)
        config["steps"] = 3
        saved = client.put(
            f"/api/jobs/{job['id']}",
            json={
                "name": job["name"],
                "family": "wan",
                "config": config,
                "datasets": job["datasets"],
                "revision": job["revision"],
            },
        )
        assert saved.status_code == 200, saved.text
        second = service.prepare(job["id"])
        prepared = wait_for(service, second["id"], "prepared", device_released=True)
        caches = [stage for stage in prepared["stages"] if stage["name"] in {"latent_cache", "text_cache"}]
        assert [stage["status"] for stage in caches] == ["reused", "reused"]
        assert all(stage["reused_from"] == first["id"] for stage in caches)
        updated_datasets = [{"dataset_id": ids[0], "resolution": [512, 256]}, job["datasets"][1]]
        changed = client.put(
            f"/api/jobs/{job['id']}",
            json={
                "name": job["name"],
                "family": "wan",
                "config": config,
                "datasets": updated_datasets,
                "revision": saved.json()["revision"],
            },
        )
        assert changed.status_code == 200, changed.text
        third = service.prepare(job["id"])
        rebuilt = wait_for(service, third["id"], "prepared", device_released=True)
        assert [stage["status"] for stage in rebuilt["stages"] if stage["name"] in {"latent_cache", "text_cache"}] == [
            "completed",
            "completed",
        ]
        native = tomllib.loads((Path(third["directory"]) / "dataset.toml").read_text(encoding="utf-8"))
        assert [section["resolution"] for section in native["datasets"]] == [[512, 256], [256, 320]]
        assert native["general"]["resolution"] == [256, 256]
        assert any(Path(first["cache"]).glob("dataset*/*.safetensors"))
    finally:
        service.close()

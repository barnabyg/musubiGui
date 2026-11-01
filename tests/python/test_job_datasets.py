"""A job lists several datasets; version 1 revisions, exports and Runs keep working."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import acceptance_workspace, wait_for

from musubi_gui.app import create_app
from musubi_gui.jobs import Job, JobConfig, JobStore, as_draft, smoke_config
from musubi_gui.outputs import compare_runs
from musubi_gui.runs import RunService
from musubi_gui.snapshots import cache_stage_keys
from musubi_gui.training_sections import dataset_toml, plan_sections

CONFIG = smoke_config("wan").model_dump()


def job_files(state: Path, job_id: str) -> Path:
    return state / "jobs" / job_id


def version_1_revision(state: Path, dataset_id: str, job_id: str = "a" * 32) -> Path:
    """A revision file exactly as the previous application version wrote it."""
    directory = job_files(state, job_id)
    directory.mkdir(parents=True)
    file = directory / "1.json"
    file.write_text(
        json.dumps(
            {
                "name": "Old job",
                "family": "wan",
                "dataset_id": dataset_id,
                "config": CONFIG,
                "version": 1,
                "id": job_id,
                "revision": 1,
            }
        ),
        encoding="utf-8",
    )
    return file


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    assets = tmp_path / "assets"
    assets.mkdir()
    return TestClient(create_app(tmp_path / "state", assets, fixture_mode=True, device_path=tmp_path / "device.sqlite"))


def test_a_version_1_revision_loads_unchanged_and_saving_it_creates_a_version_2_revision(
    tmp_path: Path, client: TestClient
) -> None:
    original = version_1_revision(tmp_path / "state", "d" * 32)
    before = original.read_bytes()
    job = client.get(f"/api/jobs/{'a' * 32}").json()
    assert job["datasets"] == [{"dataset_id": "d" * 32, "repeats": None}]
    assert "dataset_id" not in job
    saved = client.put(
        f"/api/jobs/{job['id']}",
        json={"name": job["name"], "family": job["family"], "datasets": job["datasets"], "revision": 1},
    )
    assert saved.status_code == 200, saved.text
    assert original.read_bytes() == before
    written = json.loads((original.parent / "2.json").read_text(encoding="utf-8"))
    assert written["version"] == 2
    assert written["datasets"] == [{"dataset_id": "d" * 32, "repeats": None}]
    assert "dataset_id" not in written
    assert client.get(f"/api/jobs/{job['id']}/revisions/1").json()["datasets"] == written["datasets"]


def test_an_empty_version_1_dataset_is_an_empty_list(tmp_path: Path, client: TestClient) -> None:
    version_1_revision(tmp_path / "state", "")
    assert client.get(f"/api/jobs/{'a' * 32}").json()["datasets"] == []


def test_a_version_1_export_imports_and_new_exports_are_version_2(client: TestClient) -> None:
    imported = client.post(
        "/api/jobs/import",
        json={
            "version": 1,
            "format": "musubi-gui-job",
            "draft": {"name": "Old", "family": "wan", "dataset_id": "d" * 32, "config": {"rank": 16}},
        },
    )
    assert imported.status_code == 200, imported.text
    job = imported.json()
    assert job["datasets"] == [{"dataset_id": "d" * 32, "repeats": None}]
    assert job["config"]["rank"] == 16
    exported = client.get(f"/api/jobs/{job['id']}/export").json()
    assert exported["version"] == 2
    assert exported["draft"]["datasets"] == job["datasets"]
    assert "dataset_id" not in exported["draft"]
    again = client.post("/api/jobs/import", json=exported)
    assert again.status_code == 200, again.text
    assert again.json()["datasets"] == job["datasets"]


@pytest.mark.parametrize(
    ("version", "draft"),
    [
        (99, {"datasets": []}),
        (1, {"datasets": []}),
        (2, {"dataset_id": "d"}),
        (2, {"datasets": [], "unknown_switch": True}),
        (2, {"datasets": [{"dataset_id": "d", "weight": 2}]}),
    ],
)
def test_unknown_versions_fields_and_mismatched_shapes_are_rejected(
    client: TestClient, version: int, draft: dict[str, Any]
) -> None:
    response = client.post("/api/jobs/import", json={"version": version, "format": "musubi-gui-job", "draft": draft})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 99, "dataset_id": ""},
        {"version": 1, "dataset_id": "", "unknown": True},
        {"version": 1, "datasets": []},
        {"version": 2, "dataset_id": ""},
    ],
)
def test_unknown_stored_revisions_stay_blocked_and_preserved(
    tmp_path: Path, client: TestClient, stored: dict[str, Any]
) -> None:
    file = version_1_revision(tmp_path / "state", "")
    content = json.dumps({"name": "n", "family": "wan", "id": "a" * 32, "revision": 1, **stored})
    file.write_text(content, encoding="utf-8")
    assert client.get(f"/api/jobs/{'a' * 32}").status_code == 422
    assert file.read_text(encoding="utf-8") == content


def test_api_requests_may_still_send_the_legacy_dataset_id_but_not_both(client: TestClient) -> None:
    created = client.post("/api/jobs", json={"name": "Legacy", "dataset_id": "d" * 32}).json()
    assert created["datasets"] == [{"dataset_id": "d" * 32, "repeats": None}]
    both = client.post("/api/jobs", json={"name": "Both", "dataset_id": "d", "datasets": []})
    assert both.status_code == 422


def test_duplicates_and_conversion_keep_the_dataset_list(client: TestClient) -> None:
    datasets = [{"dataset_id": "d" * 32, "repeats": 3}, {"dataset_id": "e" * 32, "repeats": None}]
    job = client.post("/api/jobs", json={"name": "Two", "family": "wan", "datasets": datasets}).json()
    assert job["datasets"] == datasets
    assert client.post(f"/api/jobs/{job['id']}/duplicate", json={}).json()["datasets"] == datasets
    converted = client.post(f"/api/jobs/{job['id']}/convert", json={"family": "h3", "revision": 1, "confirmed": True})
    assert converted.json()["datasets"] == datasets


def registered(client: TestClient, workspace: dict[str, Any], family: str) -> str:
    return str(next(job for job in workspace["jobs"] if job["family"] == family)["dataset_id"])


def fixture_client(tmp_path: Path, workspace: dict[str, Any]) -> TestClient:
    assets = tmp_path / "assets"
    assets.mkdir()
    return TestClient(
        create_app(Path(workspace["state"]), assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    )


def test_review_rejects_the_same_dataset_twice_and_a_deleted_dataset(tmp_path: Path) -> None:
    workspace = acceptance_workspace(tmp_path)
    client = fixture_client(tmp_path, workspace)
    dataset_id = registered(client, workspace, "wan")
    twice = client.post(
        "/api/jobs",
        json={"name": "Twice", "family": "wan", "datasets": [{"dataset_id": dataset_id}, {"dataset_id": dataset_id}]},
    ).json()
    review = client.post(f"/api/jobs/{twice['id']}/review", json={}).json()
    assert any("listed more than once" in error for error in review["errors"])
    gone = client.post("/api/jobs", json={"name": "Gone", "datasets": [{"dataset_id": "f" * 32}]}).json()
    response = client.post(f"/api/jobs/{gone['id']}/review", json={})
    assert response.status_code == 200, response.text
    assert any(error.startswith(f'Dataset "{"f" * 32}"') for error in response.json()["errors"])


def test_review_rejects_row_repeats_below_one(tmp_path: Path, client: TestClient) -> None:
    job = client.post("/api/jobs", json={"name": "Zero", "datasets": [{"dataset_id": "d", "repeats": 0}]}).json()
    errors = client.post(f"/api/jobs/{job['id']}/review", json={}).json()["errors"]
    assert any(error.startswith("Dataset:") and "repeats" in error for error in errors)


ITEMS: list[dict[str, Any]] = [
    {
        "id": "a",
        "path": "a.png",
        "kind": "image",
        "metadata": {},
        "sha256": "1" * 64,
        "caption": {"text": "An image.", "exists": True},
        "audio": None,
    },
    {
        "id": "b",
        "path": "b.mp4",
        "kind": "video",
        "metadata": {"fps": 16.0},
        "sha256": "2" * 64,
        "caption": {"text": "A clip.", "exists": True},
        "audio": {"source": "missing"},
    },
    {
        "id": "c",
        "path": "c.mp4",
        "kind": "video",
        "metadata": {"fps": 30.0},
        "sha256": "3" * 64,
        "caption": {"text": "", "exists": False},
        "audio": {"source": "missing"},
    },
]
CHECKOUT = {"revision": "r", "source_digest": "s"}
ENVIRONMENT = {"python": "3.12"}


def one_dataset_job(family: str, config: JobConfig) -> Job:
    return Job(
        id="f" * 32,
        revision=1,
        name="n",
        family=family,  # type: ignore[arg-type]
        datasets=[{"dataset_id": "d"}],  # type: ignore[list-item]
        config=config,
    )


# Computed by the application version before jobs listed several datasets, whose caches must stay reusable.
def test_one_dataset_stage_keys_match_the_values_from_before_dataset_lists() -> None:
    wan_models = {
        "wan_vae": {"path": "vae", "bytes": 1, "modified_ns": 2, "header_sha256": "h"},
        "wan_t5": {"path": "t5", "bytes": 3, "modified_ns": 4, "header_sha256": None},
        "wan_low": {"path": "low", "bytes": 5, "modified_ns": 6, "header_sha256": "l"},
    }
    keys, _, _ = cache_stage_keys(one_dataset_job("wan", smoke_config("wan")), ITEMS, wan_models, CHECKOUT, ENVIRONMENT)
    assert keys == {
        "latent_cache": "32a4df9d456ad793aca4f3983305d30df517a1cda0740a746d8b5cf786e0d779",
        "text_cache": "80e9c8467ad3c0fd9a481b456734359a03a6bb69a389ed1ca23f272d9a1eb9ae",
    }
    h3_models = {
        "h3_video_vae": {"path": "vae", "bytes": 1, "modified_ns": 2, "header_sha256": "h"},
        "h3_audio_vae": {"path": "avae", "bytes": 1, "modified_ns": 2, "header_sha256": "h"},
        "h3_text": {"path": "t", "bytes": 3, "modified_ns": 4, "header_sha256": None},
    }
    keys, _, _ = cache_stage_keys(
        one_dataset_job("h3", smoke_config("h3")), ITEMS[1:2], h3_models, CHECKOUT, ENVIRONMENT
    )
    assert keys == {
        "latent_cache": "dff3f3c34c43d3dc1237848b0254208e961c5b03a25928959ef01a388c0a6c38",
        "text_cache": "9ebd1d31b3038b0ffd783813a7968b3a8af4e12f86730b0d6a10fe5fd718cfe9",
    }


GENERAL = (
    "[general]\nresolution = [256, 256]\nbatch_size = 1\nnum_repeats = 1\n"
    "enable_bucket = false\nbucket_no_upscale = false\n\n"
)


def test_one_dataset_toml_matches_the_text_from_before_dataset_lists() -> None:
    job = one_dataset_job("wan", smoke_config("wan"))
    items = [{**item, "dataset_id": "d"} for item in ITEMS]
    assert dataset_toml(job, plan_sections(job, items, Path("run"), Path("cache"))) == GENERAL + (
        '[[datasets]]\nimage_directory = "run/media"\ncache_directory = "cache/dataset-images"\n'
        'caption_extension = ".txt"\n\n'
        '[[datasets]]\nvideo_directory = "run/media/rate-1"\ncache_directory = "cache/dataset-rate-1"\n'
        'caption_extension = ".txt"\ntarget_frames = [17]\nframe_extraction = "head"\nsource_fps = 16.0\n\n'
        '[[datasets]]\nvideo_directory = "run/media/rate-2"\ncache_directory = "cache/dataset-rate-2"\n'
        'caption_extension = ".txt"\ntarget_frames = [17]\nframe_extraction = "head"\nsource_fps = 30.0\n'
    )


def as_run_before_dataset_lists(service: RunService, run_id: str) -> None:
    """Rewrite a Run's records into the shape the previous application version stored."""

    def downgrade(record: dict[str, Any]) -> None:
        job = record["job"]
        job["dataset_id"] = job.pop("datasets")[0]["dataset_id"]
        job["version"] = 1
        record["manifest"].pop("datasets")
        for item in record["manifest"]["items"]:
            item.pop("dataset_id")

    service.store.update(run_id, downgrade)


def test_a_run_prepared_before_dataset_lists_still_opens_compares_retries_and_restores(tmp_path: Path) -> None:
    workspace = acceptance_workspace(tmp_path)
    service = RunService(Path(workspace["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        job = next(job for job in workspace["jobs"] if job["family"] == "wan")
        run = service.prepare(job["id"], start=True)
        finished = wait_for(service, run["id"], "completed", device_released=True)
        assert finished["manifest"]["datasets"][0]["dataset_id"] == job["dataset_id"]
        assert finished["manifest"]["items"][0]["dataset_id"] == job["dataset_id"]
        as_run_before_dataset_lists(service, run["id"])
        old = service.get(run["id"])
        assert "datasets" not in old["manifest"]
        store = JobStore(Path(workspace["state"]))
        current = store.get(job["id"])
        store.save(job["id"], as_draft(current).model_copy(update={"name": "Renamed"}), current.revision)
        newer = service.prepare(job["id"])
        assert newer["id"] != run["id"]
        changes = compare_runs(old, service.get(newer["id"]))["changes"]
        assert any(change["path"].startswith("job.dataset") for change in changes)
        wait_for(service, newer["id"], "prepared", device_released=True)

        states = service.saved_states(run["id"])
        restored = service.restore_state(run["id"], states[0]["id"], 1, confirmed=True)
        assert restored["job"]["dataset_id"] == job["dataset_id"]
        wait_for(service, restored["id"], "completed", device_released=True)

        service.store.update(run["id"], lambda record: record.update(status="failed"))
        retried = service.retry(run["id"])
        assert retried["manifest"]["items"] == old["manifest"]["items"]
        wait_for(service, retried["id"], "prepared", device_released=True)
    finally:
        service.close()

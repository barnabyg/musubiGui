import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from helpers import acceptance_workspace, wan_review

from musubi_gui.app import create_app
from musubi_gui.runs import RunService


def fixture_client(tmp_path: Path, fixture: dict[str, Any]) -> TestClient:
    assets = tmp_path / "assets"
    assets.mkdir()
    return TestClient(
        create_app(Path(fixture["state"]), assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    )


def revise(client: TestClient, job_id: str, **changes: Any) -> dict[str, Any]:
    saved = client.get(f"/api/jobs/{job_id}").json()
    update = {key: saved[key] for key in ["name", "family", "dataset_id", "revision"]}
    response = client.put(f"/api/jobs/{job_id}", json={**update, "config": {**saved["config"], **changes}})
    assert response.status_code == 200, response.text
    revised: dict[str, Any] = response.json()
    return revised


def test_wan_slide_extraction_emits_its_stride_and_counts_windows_after_resampling(tmp_path: Path) -> None:
    review = wan_review(
        tmp_path,
        {"native.mp4": (Fraction(16), 90), "thirty.mp4": (Fraction(30), 90)},
        frames=17,
        frame_extraction="slide",
        frame_stride=8,
    )
    assert review["valid"] is True, review["errors"]
    assert 'frame_extraction = "slide"\nframe_stride = 8\n' in review["dataset_toml"]
    windows = {item["name"]: item["windows"] for item in review["video_windows"]["items"]}
    # 90 native frames: starts 0, 8, …, 72. 30 fps keeps 48 frames: starts 0, 8, 16, 24.
    assert windows == {"native.mp4": 10, "thirty.mp4": 4}
    assert review["video_windows"]["total"] == 14


def test_wan_chunk_and_uniform_extraction_count_distinct_cached_windows(tmp_path: Path) -> None:
    chunk = wan_review(tmp_path / "chunk", {"thirty.mp4": (Fraction(30), 90)}, frames=17, frame_extraction="chunk")
    assert 'frame_extraction = "chunk"\nsource_fps' in chunk["dataset_toml"]
    # 48 resampled frames hold two whole 17-frame chunks (0 and 17).
    assert chunk["video_windows"]["total"] == 2
    uniform = wan_review(
        tmp_path / "uniform",
        {"thirty.mp4": (Fraction(30), 90), "exact.mp4": (Fraction(16), 17)},
        frames=17,
        frame_extraction="uniform",
        frame_sample=3,
    )
    assert 'frame_extraction = "uniform"\nframe_sample = 3\n' in uniform["dataset_toml"]
    windows = {item["name"]: item["windows"] for item in uniform["video_windows"]["items"]}
    # Starts 0, 15, 31 over 48 frames; a clip of exactly 17 frames repeats start 0 and caches once.
    assert windows == {"thirty.mp4": 3, "exact.mp4": 1}


def test_h3_blocks_frame_extraction_other_than_head(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    job = next(job for job in fixture["jobs"] if job["family"] == "h3")
    client = fixture_client(tmp_path, fixture)
    revised = revise(client, job["id"], frame_extraction="slide")
    review = client.post(f"/api/jobs/{revised['id']}/review", json={}).json()
    assert review["valid"] is False
    assert any(error.startswith("Frame extraction: H3 uses head only") for error in review["errors"])


def test_saved_version_one_jobs_without_extraction_fields_keep_head_toml(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    client = fixture_client(tmp_path, fixture)
    expected = {
        "wan": 'target_frames = [17]\nframe_extraction = "head"\nsource_fps = 16.0\n',
        "h3": 'target_frames = [124]\nframe_extraction = "head"\n',
    }
    for family, ending in expected.items():
        job = next(job for job in fixture["jobs"] if job["family"] == family)
        stored = Path(fixture["state"]) / "jobs" / job["id"] / "1.json"
        legacy = json.loads(stored.read_text(encoding="utf-8"))
        for key in ["frame_extraction", "frame_stride", "frame_sample"]:
            del legacy["config"][key]
        stored.write_text(json.dumps(legacy), encoding="utf-8")
        assert client.get(f"/api/jobs/{job['id']}").json()["config"]["frame_extraction"] == "head"
        review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
        assert review["valid"] is True, review["errors"]
        assert review["dataset_toml"].endswith(ending)


def test_changing_wan_frame_extraction_invalidates_latent_and_text_caches(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    job = next(job for job in fixture["jobs"] if job["family"] == "wan")
    client = fixture_client(tmp_path, fixture)
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        keys = [service.prepare(job["id"])["stage_keys"]]
        for change in [{"frame_extraction": "slide", "frame_stride": 1}, {"frame_stride": 2}]:
            revise(client, job["id"], **change)
            keys.append(service.prepare(job["id"])["stage_keys"])
        for stage in ["latent_cache", "text_cache"]:
            assert len({key[stage] for key in keys}) == 3
    finally:
        service.close()


def test_invalid_slide_stride_reports_a_review_error_instead_of_failing(tmp_path: Path) -> None:
    review = wan_review(
        tmp_path, {"native.mp4": (Fraction(16), 90)}, frames=17, frame_extraction="slide", frame_stride=0
    )
    assert review["valid"] is False
    assert "Frame extraction: enter a slide stride of at least one frame." in review["errors"]
    assert not any("Shorten the job" in error for error in review["errors"])

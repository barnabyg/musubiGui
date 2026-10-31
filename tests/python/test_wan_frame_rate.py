import time
import tomllib
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
from fastapi.testclient import TestClient
from helpers import acceptance_workspace

from musubi_gui.acceptance import synthetic_video
from musubi_gui.app import create_app
from musubi_gui.runs import RunService


def wan_job(tmp_path: Path, clips: dict[str, tuple[Fraction, int]], **config: Any) -> tuple[Path, str, TestClient]:
    fixture = acceptance_workspace(tmp_path)
    folder = tmp_path / "acceptance" / "datasets" / "rates"
    for name, (rate, frames) in clips.items():
        synthetic_video(folder / name, frames, rate=rate)
        (folder / name).with_suffix(".txt").write_text("A field brightens.", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    state = Path(fixture["state"])
    client = TestClient(create_app(state, assets, fixture_mode=True, device_path=tmp_path / "device.sqlite"))
    dataset = client.post("/api/datasets", json={"name": "Frame rates", "path": str(folder)}).json()
    job = client.post(
        "/api/jobs",
        json={"name": "Rates", "family": "wan", "dataset_id": dataset["id"], "config": {"frames": 33, **config}},
    ).json()
    return state, job["id"], client


def wan_review(tmp_path: Path, clips: dict[str, tuple[Fraction, int]], **config: Any) -> dict[str, Any]:
    _, job_id, client = wan_job(tmp_path, clips, **config)
    review: dict[str, Any] = client.post(f"/api/jobs/{job_id}/review", json={}).json()
    return review


def prepared(service: RunService, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] in {"prepared", "failed"}:
            return run
        time.sleep(0.05)
    raise AssertionError(service.get(run_id))


def test_wan_review_resamples_a_30_fps_clip_to_16_fps(tmp_path: Path) -> None:
    review = wan_review(tmp_path, {"clip.mp4": (Fraction(30), 90)})
    assert review["valid"] is True, review["errors"]
    assert "source_fps = 30.0" in review["dataset_toml"]
    assert any("clip.mp4" in warning and "30" in warning and "16 fps" in warning for warning in review["warnings"])
    [item] = review["video_windows"]["items"]
    # 90 frames over 3 s keep 48 frames at 16 fps (the pinned loader drops frames).
    assert (item["source_frames"], item["resampled_frames"], item["windows"]) == (90, 48, 1)


def test_wan_review_blocks_clips_too_short_after_resampling_or_below_16_fps(tmp_path: Path) -> None:
    # 40 frames at 30 fps would satisfy 33 frames as-is, but only 21 remain at 16 fps (1.31 s < 2.06 s).
    review = wan_review(tmp_path, {"short.mp4": (Fraction(30), 40), "slow.mp4": (Fraction(12), 60)})
    assert review["valid"] is False
    short = [error for error in review["errors"] if "short.mp4" in error]
    assert short and "21 frames" in short[0] and "2.06 s" in short[0]
    assert any("slow.mp4" in error and "below" in error and "16 fps" in error for error in review["errors"])


def test_mixed_wan_frame_rates_prepare_one_resampled_section_per_rate(tmp_path: Path) -> None:
    state, job_id, client = wan_job(
        tmp_path, {"ntsc.mp4": (Fraction(30000, 1001), 90), "thirty.mp4": (Fraction(30), 90)}
    )
    review = client.post(f"/api/jobs/{job_id}/review", json={}).json()
    assert review["valid"] is True, review["errors"]
    assert any("2 frame rates" in warning for warning in review["warnings"])
    service = RunService(state, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = prepared(service, service.prepare(job_id)["id"])
        assert run["status"] == "prepared", run.get("error")
        sections = tomllib.loads((Path(run["directory"]) / "dataset.toml").read_text(encoding="utf-8"))["datasets"]
        assert len(sections) == 2
        for section in sections:
            [video] = list(Path(section["video_directory"]).glob("*.mp4"))
            with av.open(str(video)) as media:
                assert section["source_fps"] == float(media.streams.video[0].average_rate)
        assert len({section["cache_directory"] for section in sections}) == 2
        cached = {artifact["path"] for stage in run["stages"] for artifact in stage.get("artifacts", [])}
        assert {Path(path).parent.name for path in cached} == {"dataset-rate-1", "dataset-rate-2"}
    finally:
        service.close()


def test_h3_preparation_still_ignores_source_fps(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    job = next(job for job in fixture["jobs"] if job["family"] == "h3")
    service = RunService(Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = prepared(service, service.prepare(job["id"])["id"])
        assert run["status"] == "prepared", run.get("error")
        native = (Path(run["directory"]) / "dataset.toml").read_text(encoding="utf-8")
        assert "video_jsonl_file = " in native and "source_fps" not in native
    finally:
        service.close()

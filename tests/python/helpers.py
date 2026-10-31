"""Helpers shared across test modules."""

import base64
import json
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.acceptance import prepare, synthetic_video
from musubi_gui.app import create_app
from musubi_gui.datasets_api import dataset_router
from musubi_gui.runs import RunService

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def acceptance_workspace(tmp_path: Path) -> dict[str, Any]:
    """A fixture workspace without the mixed-media library scan, which no pytest test reads."""
    return prepare(tmp_path / "acceptance", "fixture", None, scan_library=False)


def wait_for(service: RunService, run_id: str, status: str, *, device_released: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == status and (not device_released or service.device.owner() is None):
            return run
        if run["status"] == "failed":
            raise AssertionError(run)
        time.sleep(0.02)
    raise AssertionError(service.get(run_id))


def fixture(tmp_path: Path) -> tuple[TestClient, Path, Path]:
    state = tmp_path / "state"
    state.mkdir()
    media = tmp_path / "资料 with spaces"
    media.mkdir()
    (media / "red.png").write_bytes(PNG)
    (media / "broken.mp4").write_bytes(b"not a video")
    (media / "red.txt").write_text("A red square", encoding="utf-8")
    (state / "settings.json").write_text(
        json.dumps({"version": 1, "dataset_roots": [str(media)]}),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(dataset_router(state))
    return TestClient(app), media, state


def wan_job(tmp_path: Path, clips: dict[str, tuple[Fraction, int]], **config: Any) -> tuple[Path, str, TestClient]:
    workspace = acceptance_workspace(tmp_path)
    folder = tmp_path / "acceptance" / "datasets" / "rates"
    for name, (rate, frames) in clips.items():
        synthetic_video(folder / name, frames, rate=rate)
        (folder / name).with_suffix(".txt").write_text("A field brightens.", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    state = Path(workspace["state"])
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

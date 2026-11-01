import json
import shutil
import wave
from pathlib import Path
from typing import Any

import av
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.dataset_fixture import add_h3_cases, prepare
from musubi_gui.datasets_api import dataset_router


def video(path: Path, width: int = 256, frames: int = 124) -> None:
    with av.open(str(path), "w") as container:
        stream: Any = container.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = width, 256, "yuv420p"
        for _ in range(frames):
            frame = av.VideoFrame(width, 256, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([128]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def audio(path: Path, seconds: float = 124 / 24) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(b"\0\0" * round(seconds * 32000))


def test_h3_inspector_resolves_sources_and_distinguishes_missing_ambiguous_malformed_short_audio(
    tmp_path: Path,
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    video(media / "missing.mp4")
    for name in ["sidecar", "explicit", "ambiguous", "malformed", "short"]:
        shutil.copyfile(media / "missing.mp4", media / f"{name}.mp4")
    video(media / "bad-geometry.mp4", width=258)
    audio(media / "sidecar.wav")
    audio(media / "explicit.wav", seconds=0.5)
    audio(media / "chosen.wav")
    audio(media / "ambiguous.wav")
    audio(media / "ambiguous.flac")
    (media / "malformed.wav").write_bytes(b"not audio")
    audio(media / "short.wav", seconds=0.5)
    state = tmp_path / "state"
    state.mkdir()
    (state / "settings.json").write_text(json.dumps({"version": 1, "dataset_roots": [str(media)]}), encoding="utf-8")
    app = FastAPI()
    app.include_router(dataset_router(state))
    client = TestClient(app)
    dataset_id = client.post("/api/datasets", json={"name": "H3 audio cases", "path": str(media)}).json()["id"]
    items = {item["name"]: item for item in client.get(f"/api/datasets/{dataset_id}/items").json()}

    def inspect(name: str) -> dict[str, Any]:
        response = client.get(f"/api/datasets/{dataset_id}/items/{items[name]['id']}/h3")
        assert response.status_code == 200, response.text
        return dict(response.json())

    missing = inspect("missing.mp4")
    assert missing["source"] == "missing"
    assert missing["errors"] == []
    sidecar = inspect("sidecar.mp4")
    assert sidecar["source"] == "sidecar"
    assert sidecar["coverage"] == 1
    assert "Multiple same-stem" in " ".join(inspect("ambiguous.mp4")["errors"])
    assert "decode" in " ".join(inspect("malformed.mp4")["errors"])
    assert "materially short" in " ".join(inspect("short.mp4")["errors"])
    assert "multiples of 32" in " ".join(inspect("bad-geometry.mp4")["errors"])
    explicit_url = f"/api/datasets/{dataset_id}/items/{items['explicit.mp4']['id']}/audio"
    assert client.put(explicit_url, json={"path": str(media / "chosen.wav")}).status_code == 200
    explicit = inspect("explicit.mp4")
    assert explicit["source"] == "explicit"
    assert explicit["errors"] == []
    assert client.put(explicit_url, json={"path": str(tmp_path / "outside.wav")}).status_code == 422


def test_h3_embedded_timeline_and_target_frame_rules(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    media = prepare(root)
    add_h3_cases(media)
    app = FastAPI()
    app.include_router(dataset_router(root / "state"))
    client = TestClient(app)
    dataset_id = client.post("/api/datasets", json={"name": "Timelines", "path": str(media)}).json()["id"]
    items = {item["name"]: item for item in client.get(f"/api/datasets/{dataset_id}/items").json()}
    url = f"/api/datasets/{dataset_id}/items/{items['embedded.mp4']['id']}/h3"
    inspected = client.get(url).json()
    assert inspected["source"] == "embedded"
    assert inspected["errors"] == []
    assert inspected["coverage"] > 0.99
    assert "17*n+5" in " ".join(client.get(url + "?frames=125").json()["errors"])
    late_url = f"/api/datasets/{dataset_id}/items/{items['late.mp4']['id']}/h3"
    late = client.get(late_url).json()
    assert late["source"] == "embedded"
    assert late["offset_seconds"] > 0.3
    assert "misaligned" in " ".join(late["errors"])


def test_dataset_audio_coverage_counts_clips_and_not_images(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    media = prepare(root)
    app = FastAPI()
    app.include_router(dataset_router(root / "state"))
    client = TestClient(app)
    dataset_id = client.post("/api/datasets", json={"name": "Mixed", "path": str(media)}).json()["id"]
    report = client.get(f"/api/datasets/{dataset_id}/h3").json()
    # Two images, one valid clip without audio, and an invalid video that isn't included.
    assert [item["name"] for item in report["items"]] == ["motion.mp4"]
    assert report["included"] == 1
    assert report["invalid"] == 1
    items = client.get(f"/api/datasets/{dataset_id}/items").json()
    red = next(item["id"] for item in items if item["name"] == "red.png")
    assert client.post(f"/api/datasets/{dataset_id}/exclusions", json={"item_ids": [red], "excluded": True}).is_success
    assert client.get(f"/api/datasets/{dataset_id}/h3").json()["excluded"] == 0

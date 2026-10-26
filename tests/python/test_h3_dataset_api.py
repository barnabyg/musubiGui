import json
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
    for name in ["missing", "sidecar", "explicit", "ambiguous", "malformed", "short"]:
        video(media / f"{name}.mp4")
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

    assert inspect("missing.mp4")["source"] == "missing"
    assert inspect("missing.mp4")["errors"] == []
    assert inspect("sidecar.mp4")["source"] == "sidecar"
    assert inspect("sidecar.mp4")["coverage"] == 1
    assert "Multiple same-stem" in " ".join(inspect("ambiguous.mp4")["errors"])
    assert "decode" in " ".join(inspect("malformed.mp4")["errors"])
    assert "materially short" in " ".join(inspect("short.mp4")["errors"])
    assert "multiples of 32" in " ".join(inspect("bad-geometry.mp4")["errors"])
    explicit_url = f"/api/datasets/{dataset_id}/items/{items['explicit.mp4']['id']}/audio"
    assert client.put(explicit_url, json={"path": str(media / "chosen.wav")}).status_code == 200
    assert inspect("explicit.mp4")["source"] == "explicit"
    assert inspect("explicit.mp4")["errors"] == []
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

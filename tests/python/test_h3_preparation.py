import shutil
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from musubi_gui.acceptance import synthetic_video
from musubi_gui.dataset_fixture import write_tone
from musubi_gui.datasets import DatasetStore, content_hash
from musubi_gui.datasets_api import dataset_router
from musubi_gui.h3_preparation import H3Preparation, H3PreparationRequest
from musubi_gui.h3_prepare_worker import prepare_audio
from musubi_gui.settings import Settings, save_settings


@pytest.mark.parametrize("rotation", [0, 90])
def test_mixed_orientation_preparation_retains_portrait_head(tmp_path: Path, rotation: int) -> None:
    client, media, dataset_id, _ = setup(tmp_path)
    image = Image.new("RGB", (192, 320), "blue")
    ImageDraw.Draw(image).rectangle((72, 12, 120, 48), fill="red")
    image = image.rotate(-rotation, expand=True)
    with av.open(str(media / "portrait.mp4"), "w") as output:
        stream: Any = output.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = image.width, image.height, "yuv420p"
        stream.set_display_rotation(rotation)
        frame_type: Any = av.VideoFrame
        for _ in range(124):
            for packet in stream.encode(frame_type.from_image(image)):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    client.post(f"/api/datasets/{dataset_id}/scan?wait=true", json={})
    response = client.post(
        f"/api/datasets/{dataset_id}/h3-preparation",
        json={
            "name": "Mixed prepared",
            "output_root": str(tmp_path),
            "width": 320,
            "height": 192,
            "allow_missing_audio": True,
        },
    )
    assert response.status_code == 200, response.text
    record = client.get(f"/api/datasets/{dataset_id}/h3-preparation").json()
    items = client.get(f"/api/datasets/{record['dataset_id']}/items").json()
    portrait = next(item for item in items if item["name"].startswith("portrait-"))
    with av.open(str(Path(record["path"]) / portrait["path"])) as output:
        first = next(output.decode(video=0)).to_image()
    # The red marker represents the head near the top of a portrait shot.
    assert first.size == (192, 320), "Portrait video was forced into a landscape crop."
    pixel = first.getpixel((96, 30))
    assert isinstance(pixel, tuple) and pixel[0] > 200 and pixel[2] < 60, "The portrait head was cropped out."


def setup(tmp_path: Path) -> tuple[TestClient, Path, str, Path]:
    media = tmp_path / "sources 资料"
    media.mkdir()
    state = tmp_path / "state"
    save_settings(state, Settings(dataset_roots=[str(tmp_path)]))
    synthetic_video(media / "talk.mp4", 180, rate=Fraction(30))
    write_tone(media / "talk.wav", 6)
    (media / "talk.txt").write_text("A person talks. 中文", encoding="utf-8")
    app = FastAPI()
    app.include_router(dataset_router(state))
    client = TestClient(app)
    registered = client.post("/api/datasets", json={"name": "Sources", "path": str(media)})
    assert registered.status_code == 200
    return client, media, registered.json()["id"], state


def test_batch_prepares_real_media_and_preserves_sources_with_per_file_failures(tmp_path: Path) -> None:
    client, media, dataset_id, state = setup(tmp_path)
    synthetic_video(media / "short.mp4", 150, rate=Fraction(30))
    (media / "broken.mp4").write_bytes(b"invalid")
    shutil.copyfile(media / "talk.mp4", media / "silent.mp4")
    client.post(f"/api/datasets/{dataset_id}/scan?wait=true", json={})
    originals = {path: content_hash(path) for path in media.iterdir()}
    response = client.post(
        f"/api/datasets/{dataset_id}/h3-preparation",
        json={
            "name": "Prepared",
            "output_root": str(tmp_path),
            "width": 320,
            "height": 192,
        },
    )
    assert response.status_code == 200, response.text
    record = client.get(f"/api/datasets/{dataset_id}/h3-preparation").json()
    assert record["state"] == "complete", record
    assert record["completed"] == record["total"] == 4
    outcomes = {item["name"]: item for item in record["items"]}
    assert outcomes["talk.mp4"]["state"] == "complete", outcomes
    assert "too short" in outcomes["short.mp4"]["error"]
    assert "No audio" in outcomes["silent.mp4"]["error"]
    assert outcomes["broken.mp4"]["state"] == "failed"
    assert {path: content_hash(path) for path in media.iterdir()} == originals
    destination = Path(record["path"])
    assert not list(destination.glob(".preparing-*"))
    prepared = client.get(f"/api/datasets/{record['dataset_id']}/items").json()
    assert len(prepared) == 1
    item = prepared[0]
    assert item["caption"]["text"] == "A person talks. 中文"
    checked = client.get(f"/api/datasets/{record['dataset_id']}/items/{item['id']}/h3").json()
    assert checked["errors"] == [], checked
    assert checked["coverage"] == 1
    assert checked["metadata"]["width"] == 320
    assert checked["metadata"]["height"] == 192
    assert checked["metadata"]["frames"] == 124
    assert checked["metadata"]["fps"] == 24
    output = destination / item["path"]
    with wave.open(str(output.with_suffix(".wav"))) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getnframes()) == (32000, 2, round(124 / 24 * 32000))
        assert any(audio.readframes(audio.getnframes()))
    # Reopening the service retains the completed batch and its result.
    assert H3Preparation(DatasetStore(state)).status(dataset_id) == record


@pytest.mark.parametrize("fit", ["crop", "pad"])
def test_silent_video_conversion_changes_geometry_without_stretching(tmp_path: Path, fit: str) -> None:
    client, media, dataset_id, _ = setup(tmp_path)
    (media / "talk.wav").unlink()
    response = client.post(
        f"/api/datasets/{dataset_id}/h3-preparation",
        json={
            "name": "Silent prepared",
            "output_root": str(tmp_path),
            "width": 320,
            "height": 192,
            "fit": fit,
            "allow_missing_audio": True,
        },
    )
    assert response.status_code == 200
    record = client.get(f"/api/datasets/{dataset_id}/h3-preparation").json()
    assert record["items"][0]["state"] == "complete", record
    output = next(Path(record["path"]).glob("*.mp4"))
    assert not output.with_suffix(".wav").exists()
    with av.open(str(output)) as video:
        frame = next(video.decode(video=0)).to_image()
        # The square source fills the crop; fitting it leaves black bars on each side.
        pixel = frame.getpixel((2, 96))
        assert isinstance(pixel, tuple)
        assert (max(pixel) < 10) == (fit == "pad")


def test_fixed_geometry_can_keep_portrait_content_with_borders(tmp_path: Path) -> None:
    client, media, dataset_id, _ = setup(tmp_path)
    synthetic_video(media / "portrait.mp4", 124, width=192, height=320)
    client.post(f"/api/datasets/{dataset_id}/scan?wait=true", json={})
    response = client.post(
        f"/api/datasets/{dataset_id}/h3-preparation",
        json={
            "name": "Fixed geometry",
            "output_root": str(tmp_path),
            "width": 320,
            "height": 192,
            "orientation": "fixed",
            "fit": "pad",
            "allow_missing_audio": True,
        },
    )
    assert response.status_code == 200, response.text
    record = client.get(f"/api/datasets/{dataset_id}/h3-preparation").json()
    portrait = next(item for item in record["items"] if item["name"] == "portrait.mp4")
    assert portrait["state"] == "complete", portrait
    assert (portrait["width"], portrait["height"]) == (320, 192)
    with av.open(str(Path(record["path"]) / portrait["output"])) as output:
        first = next(output.decode(video=0)).to_image()
        edge = first.getpixel((2, 96))
        assert isinstance(edge, tuple) and max(edge) < 10


def test_preparation_rejects_unsafe_targets_invalid_geometry_and_overlapping_batches(tmp_path: Path) -> None:
    client, media, dataset_id, state = setup(tmp_path)
    endpoint = f"/api/datasets/{dataset_id}/h3-preparation"
    request: dict[str, Any] = {"name": "Prepared", "output_root": str(tmp_path)}
    for changes in [
        {"width": 641},
        {"frames": 125},
        {"output_root": str(media)},
        {"output_root": str(tmp_path.parent)},
        {"name": " "},
    ]:
        assert client.post(endpoint, json={**request, **changes}).status_code == 422
    service = H3Preparation(DatasetStore(state))
    record = service.begin(dataset_id, H3PreparationRequest.model_validate(request))
    with pytest.raises(ValueError, match="already running"):
        service.begin(dataset_id, H3PreparationRequest.model_validate(request))
    restarted = H3Preparation(DatasetStore(state)).status(dataset_id)
    assert restarted and restarted["state"] == "interrupted"
    assert Path(record["path"]).is_dir()
    # Removing a result entry while queued must not leave all future batches blocked.
    service.store.scan(record["dataset_id"])
    service.store.remove(record["dataset_id"])
    service.run(dataset_id)
    assert service.status(dataset_id)["state"] == "failed"
    assert not service.active


def test_embedded_audio_and_nonconforming_source_geometry_are_converted(tmp_path: Path) -> None:
    client, media, dataset_id, _ = setup(tmp_path)
    (media / "talk.wav").unlink()
    synthetic_video(media / "talk.mp4", 180, audio=True, rate=30, width=318, height=238)
    response = client.post(
        f"/api/datasets/{dataset_id}/h3-preparation",
        json={
            "name": "Embedded prepared",
            "output_root": str(tmp_path),
            "width": 320,
            "height": 192,
        },
    )
    assert response.status_code == 200
    record = client.get(f"/api/datasets/{dataset_id}/h3-preparation").json()
    assert record["items"][0]["state"] == "complete", record
    item = client.get(f"/api/datasets/{record['dataset_id']}/items").json()[0]
    checked = client.get(f"/api/datasets/{record['dataset_id']}/items/{item['id']}/h3").json()
    assert checked["errors"] == [], checked
    assert checked["coverage"] == 1
    assert checked["audio_metadata"]["sample_rate"] == 32000


def test_audio_resampling_keeps_embedded_offset_and_rejects_material_missing_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b"\xe8\x03" * (6 * 44100))
    output = tmp_path / "aligned.wav"
    # The audio starts 100 ms after the video's first frame.
    prepare_audio(source, output, 124, -0.1)
    with wave.open(str(output)) as audio:
        assert audio.getnframes() == round(124 / 24 * 32000)
        assert not any(audio.readframes(3100))
        assert any(audio.readframes(1000))
        assert any(audio.readframes(audio.getnframes())[-400:])
    with pytest.raises(ValueError, match="200 ms"):
        prepare_audio(source, tmp_path / "bad.wav", 124, -0.4)

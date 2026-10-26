import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


def fixture(tmp_path: Path) -> tuple[TestClient, Path, Path]:
    from musubi_gui.datasets_api import dataset_router

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


def test_register_preview_rescan_and_restart_preserve_dataset(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    created = client.post("/api/datasets", json={"name": "Mixed media", "path": str(media)})
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    detail = client.get(f"/api/datasets/{dataset_id}").json()
    assert detail["scan"]["state"] == "complete"
    assert detail["counts"] == {"total": 2, "valid": 1, "included": 1, "captioned": 1, "excluded": 0}
    items = client.get(f"/api/datasets/{dataset_id}/items").json()
    image = next(item for item in items if item["name"] == "red.png")
    assert client.get(f"/api/datasets/{dataset_id}/items/{image['id']}/media").content == PNG
    assert client.get(f"/api/datasets/{dataset_id}/items/{image['id']}/thumbnail").status_code == 200
    assert client.post("/api/datasets", json={"name": "Escape", "path": str(tmp_path)}).status_code == 422
    from musubi_gui.datasets_api import dataset_router

    restarted = FastAPI()
    restarted.include_router(dataset_router(state))
    assert TestClient(restarted).get("/api/datasets").json()[0]["id"] == dataset_id
    (media / "red.png").unlink()
    assert client.post(f"/api/datasets/{dataset_id}/scan", json={}).status_code == 200
    missing = client.get(f"/api/datasets/{dataset_id}/items/{image['id']}/media")
    assert missing.status_code == 404


def test_captions_save_atomically_detect_conflicts_and_restore_exact_revision(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets", json={"name": "Caption edits", "path": str(media)}).json()
    image = next(
        item for item in client.get(f"/api/datasets/{dataset['id']}/items").json() if item["name"] == "red.png"
    )
    url = f"/api/datasets/{dataset['id']}/items/{image['id']}/caption"
    original = client.get(url).json()
    changed = client.put(url, json={"text": "红色\nsecond line", "revision": original["revision"]})
    assert changed.status_code == 200, changed.text
    assert (media / "red.txt").read_text(encoding="utf-8") == "红色\nsecond line"
    assert client.put(url, json={"text": "stale", "revision": original["revision"]}).status_code == 409
    history = client.get(url + "/history").json()
    (media / "red.txt").write_text("external editor", encoding="utf-8")
    assert (
        client.post(
            url + "/restore", json={"history_id": history[0]["id"], "revision": changed.json()["revision"]}
        ).status_code
        == 409
    )
    external = client.get(url).json()
    restored = client.post(url + "/restore", json={"history_id": history[0]["id"], "revision": external["revision"]})
    assert restored.status_code == 200
    assert (media / "red.txt").read_text(encoding="utf-8") == "A red square"
    empty = client.put(url, json={"text": "", "revision": restored.json()["revision"]})
    assert empty.status_code == 200
    assert (media / "red.txt").read_bytes() == b""


def test_concurrent_caption_writes_never_silently_replace_each_other(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets", json={"name": "Concurrent captions", "path": str(media)}).json()
    image = next(
        item for item in client.get(f"/api/datasets/{dataset['id']}/items").json() if item["name"] == "red.png"
    )
    url = f"/api/datasets/{dataset['id']}/items/{image['id']}/caption"
    revision = client.get(url).json()["revision"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda text: client.put(url, json={"text": text, "revision": revision}), ["First draft", "Second draft"]
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (media / "red.txt").read_text(encoding="utf-8") in {"First draft", "Second draft"}

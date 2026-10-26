import base64
import json
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

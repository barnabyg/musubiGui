import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_datasets_api import PNG, fixture

from musubi_gui.datasets_api import dataset_router


def test_managed_uploads_publish_only_valid_complete_files_and_never_overwrite(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    created = client.post("/api/datasets/managed", json={"name": "Uploaded media", "root": str(media)})
    assert created.status_code == 200, created.text
    dataset = created.json()
    folder = Path(dataset["path"])
    url = f"/api/datasets/{dataset['id']}/uploads"
    upload = client.post(url, json={"filename": "红 square.png", "size": len(PNG)}).json()
    upload_id = upload["id"]
    first = PNG[:20]
    assert (
        client.put(f"{url}/{upload_id}", json={"offset": 0, "data": base64.b64encode(first).decode()}).status_code
        == 200
    )
    assert not (folder / "红 square.png").exists()
    assert client.post(f"{url}/{upload_id}/complete", json={}).status_code == 409
    assert client.get(f"{url}/{upload_id}").json()["received"] == 20
    assert (
        client.put(f"{url}/{upload_id}", json={"offset": 20, "data": base64.b64encode(PNG[20:]).decode()}).status_code
        == 200
    )
    assert client.post(f"{url}/{upload_id}/complete", json={}).status_code == 200
    assert (folder / "红 square.png").read_bytes() == PNG
    assert client.post(url, json={"filename": "红 square.png", "size": len(PNG)}).status_code == 409
    assert client.post(url, json={"filename": "../escape.png", "size": 1}).status_code == 422
    assert client.post(url, json={"filename": "CON.png", "size": 1}).status_code == 422
    invalid = client.post(url, json={"filename": "invalid.mp4", "size": 5}).json()
    assert (
        client.put(
            f"{url}/{invalid['id']}", json={"offset": 0, "data": base64.b64encode(b"wrong").decode()}
        ).status_code
        == 200
    )
    assert client.post(f"{url}/{invalid['id']}/complete", json={}).status_code == 422
    assert not (folder / "invalid.mp4").exists()
    assert client.post(f"{url}/{invalid['id']}/cancel", json={}).status_code == 200
    caption = "Caption paired by Unicode stem.\n第二行".encode()
    text_upload = client.post(url, json={"filename": "红 square.txt", "size": len(caption)}).json()
    client.put(f"{url}/{text_upload['id']}", json={"offset": 0, "data": base64.b64encode(caption).decode()})
    assert client.post(f"{url}/{text_upload['id']}/complete", json={}).status_code == 200
    items = client.get(f"/api/datasets/{dataset['id']}/items").json()
    assert len(items) == 1
    assert items[0]["caption"]["text"] == caption.decode()


def test_failed_disk_flush_resumes_after_restart_and_verifies_the_existing_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, media, state = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "Recovery", "root": str(media)}).json()
    folder = Path(dataset["path"])
    url = f"/api/datasets/{dataset['id']}/uploads"
    session = client.post(url, json={"filename": "resumed.png", "size": len(PNG)}).json()

    def exhausted(_: int) -> None:
        raise OSError("Disk full fixture")

    with monkeypatch.context() as patch:
        patch.setattr("os.fsync", exhausted)
        response = client.put(f"{url}/{session['id']}", json={"offset": 0, "data": base64.b64encode(PNG[:20]).decode()})
    assert response.status_code == 500
    assert not (folder / "resumed.png").exists()
    restarted = FastAPI()
    restarted.include_router(dataset_router(state))
    client = TestClient(restarted)
    assert client.get(f"{url}/{session['id']}").json()["received"] == 20
    wrong = client.put(f"{url}/{session['id']}", json={"offset": 0, "data": base64.b64encode(b"changed file").decode()})
    assert wrong.status_code == 409
    assert (
        client.put(f"{url}/{session['id']}", json={"offset": 0, "data": base64.b64encode(PNG).decode()}).status_code
        == 200
    )
    assert client.post(f"{url}/{session['id']}/complete", json={}).status_code == 200
    assert (folder / "resumed.png").read_bytes() == PNG

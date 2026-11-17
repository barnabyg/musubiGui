import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import PNG, fixture

from musubi_gui.datasets import DatasetStore
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


def test_a_completed_upload_joins_the_gallery_without_rescanning_the_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, media, state = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "Quick uploads", "root": str(media)}).json()
    url = f"/api/datasets/{dataset['id']}/uploads"
    probes: list[Path] = []
    probe = DatasetStore.probe

    def counted(self: DatasetStore, path: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        probes.append(path)
        return probe(self, path, *args, **kwargs)

    def rescanned(*_: Any) -> None:
        raise AssertionError("An upload must not rescan the dataset.")

    monkeypatch.setattr(DatasetStore, "probe", counted)
    monkeypatch.setattr(DatasetStore, "scan", rescanned)

    def upload(name: str, data: bytes) -> Any:
        session = client.post(url, json={"filename": name, "size": len(data)}).json()
        client.put(f"{url}/{session['id']}", json={"offset": 0, "data": base64.b64encode(data).decode()})
        assert client.get(url).json() == [{**session, "received": len(data)}]
        return client.post(f"{url}/{session['id']}/complete", json={})

    assert upload("first.png", PNG).status_code == 200
    assert len(probes) == 1, "An image's validation probe also makes its thumbnail."
    assert upload("first.txt", b"A caption").status_code == 200
    assert len(probes) == 1, "A caption needs no media probe."
    assert client.get(url).json() == [], "Only incomplete uploads are listed."
    [item] = client.get(f"/api/datasets/{dataset['id']}/items").json()
    assert (item["valid"], item["caption"]["text"]) == (True, "A caption")
    assert client.get(f"/api/datasets/{dataset['id']}/items/{item['id']}/thumbnail").status_code == 200
    counts = client.get(f"/api/datasets/{dataset['id']}").json()
    assert (counts["counts"]["captioned"], counts["scan"]) == (1, {"state": "complete", "completed": 1, "total": 1})
    assert not list((state / "thumbnails").glob("upload-*")), "No staged thumbnail is left behind."

    thumbnail = (state / "thumbnails" / f"{item['id']}.jpg").read_bytes()
    session = client.post(url, json={"filename": "second.png", "size": len(PNG)}).json()
    client.put(f"{url}/{session['id']}", json={"offset": 0, "data": base64.b64encode(PNG).decode()})
    (Path(dataset["path"]) / "second.png").write_bytes(b"appeared during upload")
    assert client.post(f"{url}/{session['id']}/complete", json={}).status_code == 409
    assert (state / "thumbnails" / f"{item['id']}.jpg").read_bytes() == thumbnail
    assert not list((state / "thumbnails").glob("upload-*")), "A refused upload leaves no staged thumbnail."

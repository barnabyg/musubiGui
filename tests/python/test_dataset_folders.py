"""A managed dataset's folder is named after it and follows it when renamed."""

import json
from pathlib import Path

import pytest
from helpers import fixture

from musubi_gui import datasets


def test_managed_dataset_folder_is_named_after_the_dataset(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    created = client.post("/api/datasets/managed", json={"name": "  frogs ", "root": str(media)})
    assert created.status_code == 200
    assert created.json()["name"] == "frogs"
    assert Path(created.json()["path"]) == media / "frogs"
    assert (media / "frogs").is_dir()


def test_creating_over_an_existing_folder_is_refused_and_leaves_it_alone(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    (media / "frogs").mkdir()
    (media / "frogs" / "keep.txt").write_text("mine", encoding="utf-8")
    response = client.post("/api/datasets/managed", json={"name": "Frogs", "root": str(media)})
    assert response.status_code == 422
    # Windows names match regardless of case; the message names the folder that is there.
    assert 'A folder named "frogs" already exists' in response.json()["detail"]
    assert (media / "frogs" / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert client.get("/api/datasets").json() == []


@pytest.mark.parametrize("name", ["a/b", "a\\b", "what?", "CON", "nul.txt", "trailing.", ".hidden", "x" * 101])
def test_names_windows_cannot_keep_as_a_folder_are_refused(tmp_path: Path, name: str) -> None:
    client, media, _ = fixture(tmp_path)
    response = client.post("/api/datasets/managed", json={"name": name, "root": str(media)})
    assert response.status_code == 422
    assert "also its folder name" in response.json()["detail"]
    assert sorted(path.name for path in media.iterdir()) == ["broken.mp4", "red.png", "red.txt"]


def test_renaming_a_managed_dataset_moves_its_folder_and_audio_path(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "frogs", "root": str(media)}).json()
    folder = Path(dataset["path"])
    (folder / "clip.png").write_bytes((media / "red.png").read_bytes())
    (folder / "clip.wav").write_bytes(b"")
    client.post(f"/api/datasets/{dataset['id']}/scan?wait=true")
    item = client.get(f"/api/datasets/{dataset['id']}/items").json()[0]
    store = datasets.DatasetStore(state)
    with store._db() as db:
        db.execute("UPDATE items SET explicit_audio=? WHERE id=?", (str(folder / "clip.wav"), item["id"]))

    renamed = client.put(f"/api/datasets/{dataset['id']}", json={"name": "toads"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "toads"
    assert Path(renamed.json()["path"]) == media / "toads"
    assert not folder.exists()
    assert (media / "toads" / "clip.png").is_file()
    items = client.get(f"/api/datasets/{dataset['id']}/items").json()
    assert [(entry["id"], entry["valid"]) for entry in items] == [(item["id"], True)]
    with store._db() as db:
        audio = db.execute("SELECT explicit_audio FROM items WHERE id=?", (item["id"],)).fetchone()[0]
    assert Path(audio) == media / "toads" / "clip.wav"


def test_a_change_of_letter_case_renames_the_folder(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "frogs", "root": str(media)}).json()
    renamed = client.put(f"/api/datasets/{dataset['id']}", json={"name": "Frogs"})
    assert renamed.status_code == 200
    assert [path.name for path in media.iterdir() if path.is_dir()] == ["Frogs"]


def test_renaming_onto_an_existing_folder_keeps_the_old_name_and_folder(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "frogs", "root": str(media)}).json()
    (media / "toads").mkdir()
    response = client.put(f"/api/datasets/{dataset['id']}", json={"name": "toads"})
    assert response.status_code == 422
    assert 'A folder named "toads" already exists' in response.json()["detail"]
    assert client.get(f"/api/datasets/{dataset['id']}").json()["name"] == "frogs"
    assert (media / "frogs").is_dir()


def test_a_folder_windows_will_not_rename_keeps_the_old_name(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "frogs", "root": str(media)}).json()
    with (media / "frogs" / "open.png").open("wb"):
        response = client.put(f"/api/datasets/{dataset['id']}", json={"name": "toads"})
    assert response.status_code == 422
    assert "Windows could not rename the dataset folder" in response.json()["detail"]
    assert client.get(f"/api/datasets/{dataset['id']}").json()["name"] == "frogs"
    assert (media / "frogs").is_dir()


def test_renaming_waits_for_a_scan(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    dataset = client.post("/api/datasets/managed", json={"name": "frogs", "root": str(media)}).json()
    with datasets.DatasetStore(state)._db() as db:
        db.execute("UPDATE datasets SET scan=? WHERE id=?", (json.dumps({"state": "scanning"}), dataset["id"]))
    response = client.put(f"/api/datasets/{dataset['id']}", json={"name": "toads"})
    assert response.status_code == 422
    assert "Wait for the dataset scan" in response.json()["detail"]
    assert (media / "frogs").is_dir()


def test_renaming_a_registered_dataset_leaves_the_users_folder_alone(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets", json={"name": "Mine", "path": str(media)}).json()
    renamed = client.put(f"/api/datasets/{dataset['id']}", json={"name": "a/b: anything"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "a/b: anything"
    assert Path(renamed.json()["path"]) == media

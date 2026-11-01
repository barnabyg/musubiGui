import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import PNG, fixture

from musubi_gui import datasets
from musubi_gui.dataset_fixture import prepare
from musubi_gui.datasets_api import dataset_router


def test_rename_and_delete_library_entry_preserve_files_and_recovery_history(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    dataset = client.post("/api/datasets", json={"name": "Original", "path": str(media)}).json()
    url = f"/api/datasets/{dataset['id']}"
    items = client.get(f"{url}/items").json()
    image = next(item for item in items if item["name"] == "red.png")
    caption_url = f"{url}/items/{image['id']}/caption"
    assert (
        client.put(caption_url, json={"text": "Edited caption", "revision": image["caption"]["revision"]}).status_code
        == 200
    )
    history = list((state / "caption-history").rglob("*.json"))
    assert history
    preserved = {path: path.read_bytes() for path in [*media.iterdir(), *history] if path.is_file()}

    assert client.put(url, json={"name": "  Renamed library  "}).json()["name"] == "Renamed library"
    assert client.get(url).json()["path"] == str(media)
    renamed_items = client.get(f"{url}/items").json()
    assert [item["id"] for item in renamed_items] == [item["id"] for item in items]
    assert next(item for item in renamed_items if item["id"] == image["id"])["caption"]["text"] == "Edited caption"
    assert client.put(url, json={"name": " "}).status_code == 422
    assert client.put(url, json={"name": "x" * 201}).status_code == 422
    assert client.put("/api/datasets/unknown", json={"name": "Missing"}).status_code == 404

    assert client.delete(url).status_code == 200
    assert client.get("/api/datasets").json() == []
    assert client.get(url).status_code == 404
    assert client.get(f"{url}/items").status_code == 404
    assert client.delete(url).status_code == 404
    assert {path: path.read_bytes() for path in preserved} == preserved
    from musubi_gui.datasets import DatasetStore

    assert DatasetStore(state).list() == []
    recreated = client.post("/api/datasets", json={"name": "Registered again", "path": str(media)}).json()
    restored = client.get(f"/api/datasets/{recreated['id']}/items").json()
    assert next(item for item in restored if item["name"] == "red.png")["caption"]["text"] == "Edited caption"


def test_delete_waits_for_pending_scan_and_preserves_managed_folder(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    from musubi_gui.datasets import DatasetStore

    store = DatasetStore(state)
    dataset = store.register("Pending", str(media))
    url = f"/api/datasets/{dataset['id']}"
    assert client.delete(url).status_code == 422
    assert client.get(url).status_code == 200
    client.post(f"{url}/scan")
    assert client.delete(url).status_code == 200
    managed = store.create_managed("Managed", str(media))
    folder = Path(managed["path"])
    (folder / "uploaded.png").write_bytes(PNG)
    (folder / "uploaded.txt").write_text("Uploaded caption", encoding="utf-8")
    store.scan(managed["id"])
    assert client.delete(f"/api/datasets/{managed['id']}").status_code == 200
    assert (folder / "uploaded.png").read_bytes() == PNG
    assert (folder / "uploaded.txt").read_text(encoding="utf-8") == "Uploaded caption"


def test_register_preview_rescan_and_restart_preserve_dataset(tmp_path: Path) -> None:
    client, media, state = fixture(tmp_path)
    created = client.post("/api/datasets", json={"name": "Mixed media", "path": str(media)})
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    detail = client.get(f"/api/datasets/{dataset_id}").json()
    assert detail["scan"]["state"] == "complete"
    assert detail["counts"] == {
        "total": 2,
        "valid": 1,
        "included": 1,
        "images": 1,
        "videos": 0,
        "captioned": 1,
        "excluded": 0,
    }
    items = client.get(f"/api/datasets/{dataset_id}/items").json()
    image = next(item for item in items if item["name"] == "red.png")
    assert client.get(f"/api/datasets/{dataset_id}/items/{image['id']}/media").content == PNG
    assert client.get(f"/api/datasets/{dataset_id}/items/{image['id']}/thumbnail").status_code == 200
    assert client.post("/api/datasets", json={"name": "Escape", "path": str(tmp_path)}).status_code == 422
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


def caption_url(client: TestClient, media: Path) -> str:
    dataset = client.post("/api/datasets", json={"name": "Locked sidecar", "path": str(media)}).json()
    image = next(
        item for item in client.get(f"/api/datasets/{dataset['id']}/items").json() if item["name"] == "red.png"
    )
    return f"/api/datasets/{dataset['id']}/items/{image['id']}/caption"


def test_a_caption_saves_when_another_process_briefly_holds_the_sidecar_open(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    url = caption_url(client, media)
    revision = client.get(url).json()["revision"]
    holder = (media / "red.txt").open("rb")
    release = threading.Timer(0.3, holder.close)
    release.start()
    try:
        saved = client.put(url, json={"text": "Saved past the lock", "revision": revision})
    finally:
        release.join()
    assert saved.status_code == 200, saved.text
    assert (media / "red.txt").read_text(encoding="utf-8") == "Saved past the lock"


def test_a_caption_save_reports_a_lasting_lock_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(datasets, "CAPTION_REPLACE_SECONDS", 0.1)
    client, media, _ = fixture(tmp_path)
    url = caption_url(client, media)
    revision = client.get(url).json()["revision"]
    with (media / "red.txt").open("rb"):
        refused = client.put(url, json={"text": "Waiting", "revision": revision})
    assert refused.status_code == 500
    assert (media / "red.txt").read_text(encoding="utf-8") == "A red square"
    assert not list(media.glob(".pending-caption-*"))
    assert client.put(url, json={"text": "Waiting", "revision": revision}).status_code == 200
    assert (media / "red.txt").read_text(encoding="utf-8") == "Waiting"


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


def test_rescan_retries_unchanged_invalid_media_after_decoder_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, media, _ = fixture(tmp_path)
    actual_run = subprocess.run

    def unavailable(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if any("media_probe.py" in value for value in args) and str(media / "red.png") in args:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Temporary decoder failure")
        return actual_run(args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", unavailable)
        dataset_id = client.post("/api/datasets", json={"name": "Decoder recovery", "path": str(media)}).json()["id"]
    before = client.get(f"/api/datasets/{dataset_id}/items").json()
    assert not next(item for item in before if item["name"] == "red.png")["valid"]
    assert client.post(f"/api/datasets/{dataset_id}/scan", json={}).status_code == 200
    after = client.get(f"/api/datasets/{dataset_id}/items").json()
    assert next(item for item in after if item["name"] == "red.png")["valid"]
    assert (media / "red.png").read_bytes() == PNG
    called: list[list[str]] = []

    def observed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        called.append(args)
        return actual_run(args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", observed)
        assert client.post(f"/api/datasets/{dataset_id}/scan", json={}).status_code == 200
    assert not any(str(media / "red.png") in command for command in called)


def test_caption_editor_rejects_inside_root_junction_retarget_without_touching_either_sidecar(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    routed, target = media / "source folder", media / "other folder"
    routed.mkdir()
    target.mkdir()
    for folder, text in [(routed, "Original intended caption"), (target, "Do not change this target")]:
        (folder / "item.png").write_bytes(PNG)
        (folder / "item.txt").write_text(text, encoding="utf-8")
    dataset_id = client.post("/api/datasets", json={"name": "Junction boundaries", "path": str(media)}).json()["id"]
    item = next(
        item
        for item in client.get(f"/api/datasets/{dataset_id}/items").json()
        if item["name"] == "source folder/item.png"
    )
    url = f"/api/datasets/{dataset_id}/items/{item['id']}/caption"
    revision = client.get(url).json()["revision"]
    original = media / "original folder"
    routed.rename(original)
    cmd = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe"
    subprocess.run(
        [str(cmd), "/d", "/c", "mklink", "/J", str(routed), str(target)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    try:
        assert client.get(url).status_code == 422
        assert client.put(url, json={"text": "Wrong destination", "revision": revision}).status_code == 422
        assert (target / "item.txt").read_text(encoding="utf-8") == "Do not change this target"
        assert (original / "item.txt").read_text(encoding="utf-8") == "Original intended caption"
    finally:
        routed.rmdir()


def test_caption_editor_rejects_a_dataset_root_replaced_by_a_junction(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    url = caption_url(client, media)
    revision = client.get(url).json()["revision"]
    target = tmp_path / "unrelated media"
    target.mkdir()
    (target / "red.png").write_bytes(PNG)
    (target / "red.txt").write_text("Do not change this target", encoding="utf-8")
    original = tmp_path / "original media"
    media.rename(original)
    cmd = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe"
    subprocess.run(
        [str(cmd), "/d", "/c", "mklink", "/J", str(media), str(target)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    try:
        response = client.put(url, json={"text": "Wrong destination", "revision": revision})
        assert response.status_code == 422
        assert "Linked or redirected" in response.json()["detail"]
        assert (target / "red.txt").read_text(encoding="utf-8") == "Do not change this target"
        assert (original / "red.txt").read_text(encoding="utf-8") == "A red square"
    finally:
        media.rmdir()


def test_caption_editor_rejects_a_sidecar_reparse_point_without_deleting_the_target(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset_id = client.post("/api/datasets", json={"name": "Sidecar boundary", "path": str(media)}).json()["id"]
    image = next(item for item in client.get(f"/api/datasets/{dataset_id}/items").json() if item["name"] == "red.png")
    url = f"/api/datasets/{dataset_id}/items/{image['id']}/caption"
    revision = client.get(url).json()["revision"]
    target = media / "preserved target"
    target.mkdir()
    (target / "keep.txt").write_text("Original unrelated data", encoding="utf-8")
    sidecar = media / "red.txt"
    sidecar.unlink()
    cmd = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe"
    subprocess.run(
        [str(cmd), "/d", "/c", "mklink", "/J", str(sidecar), str(target)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    try:
        response = client.put(url, json={"text": "Wrong target", "revision": revision})
        assert response.status_code == 422
        assert "Linked" in response.json()["detail"]
        assert (target / "keep.txt").read_text(encoding="utf-8") == "Original unrelated data"
        assert (media / "red.png").read_bytes() == PNG
    finally:
        sidecar.rmdir()


def test_contents_count_valid_included_media_and_follow_rescans(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    media = prepare(root)
    app = FastAPI()
    app.include_router(dataset_router(root / "state"))
    client = TestClient(app)
    dataset_id = client.post("/api/datasets", json={"name": "Mixed", "path": str(media)}).json()["id"]

    def counts() -> dict[str, int]:
        result: dict[str, int] = client.get(f"/api/datasets/{dataset_id}").json()["counts"]
        return result

    # Two images and a video; the invalid video counts in neither.
    assert (counts()["images"], counts()["videos"]) == (2, 1)
    assert counts()["included"] == counts()["images"] + counts()["videos"]
    items = client.get(f"/api/datasets/{dataset_id}/items").json()
    red = next(item["id"] for item in items if item["name"] == "red.png")
    assert client.post(f"/api/datasets/{dataset_id}/exclusions", json={"item_ids": [red], "excluded": True}).is_success
    assert (counts()["images"], counts()["videos"], counts()["included"]) == (1, 1, 2)
    shutil.copyfile(media / "blue.png", media / "green.png")
    assert client.post(f"/api/datasets/{dataset_id}/scan", json={}).is_success
    assert (counts()["images"], counts()["videos"], counts()["included"]) == (2, 1, 3)
    (media / "green.png").unlink()
    (media / "motion.mp4").unlink()
    assert client.post(f"/api/datasets/{dataset_id}/scan", json={}).is_success
    assert (counts()["images"], counts()["videos"], counts()["included"]) == (1, 0, 1)

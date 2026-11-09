"""Gallery I/O stays proportional to the requested media, with authoritative sidecars."""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from helpers import PNG, fixture


@pytest.mark.parametrize("size", [8, 32, 64])
def test_gallery_reads_each_sidecar_once_and_item_requests_do_not_read_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    client, media, _ = fixture(tmp_path)
    folder = media / "gallery"
    folder.mkdir()
    for index in range(size):
        (folder / f"image-{index:02}.png").write_bytes(PNG)
        (folder / f"image-{index:02}.txt").write_text(f"Caption {index}", encoding="utf-8")
    dataset = client.post("/api/datasets", json={"name": "Gallery", "path": str(folder)}).json()
    url = f"/api/datasets/{dataset['id']}/items"
    # The authoritative text changes after the scan, before the gallery opens.
    (folder / "image-00.txt").write_text("External edit", encoding="utf-8")
    reads: Counter[str] = Counter()
    original_open = Path.open

    def observed_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.suffix == ".txt" and path.is_relative_to(folder) and "r" in mode:
            reads[path.name] += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed_open)
    response = client.get(url)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == size
    image = items[0]
    assert image["caption"]["text"] == "External edit"
    assert reads == Counter({f"image-{index:02}.txt": 1 for index in range(size)})

    reads.clear()
    assert client.get(f"{url}/{image['id']}/thumbnail").status_code == 200
    assert not reads
    caption_url = f"{url}/{image['id']}/caption"
    (folder / "image-00.txt").write_text("New external edit", encoding="utf-8")
    assert (
        client.put(caption_url, json={"text": "Stale draft", "revision": image["caption"]["revision"]}).status_code
        == 409
    )
    current = client.get(caption_url).json()
    saved = client.put(caption_url, json={"text": "Saved caption", "revision": current["revision"]})
    assert saved.status_code == 200
    assert saved.json()["text"] == "Saved caption"
    assert set(reads) == {"image-00.txt"}


def test_gallery_retains_missing_media_and_unreadable_or_absent_captions(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    dataset = client.post("/api/datasets", json={"name": "Gallery errors", "path": str(media)}).json()
    url = f"/api/datasets/{dataset['id']}/items"

    def image() -> dict[str, Any]:
        response = client.get(url)
        assert response.status_code == 200
        return next(item for item in response.json() if item["name"] == "red.png")

    (media / "red.txt").unlink()
    assert image()["caption"]["exists"] is False
    (media / "red.txt").write_bytes(b"\xff")
    assert "error" in image()["caption"]
    (media / "red.png").unlink()
    missing = image()
    assert "Source media is missing" in missing["caption"]["error"]
    assert client.get(f"{url}/{missing['id']}/thumbnail").status_code == 404

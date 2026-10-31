from pathlib import Path

from fastapi.testclient import TestClient
from helpers import acceptance_workspace
from PIL import Image

from musubi_gui.app import create_app


def test_gallery_only_gif_tiff_formats_block_job_review_until_excluded_while_png_remains_supported(
    tmp_path: Path,
) -> None:
    fixture = acceptance_workspace(tmp_path)
    folder = tmp_path / "source images"
    folder.mkdir()
    for name in ["animated.gif", "scan.tif", "scan2.tiff", "supported.png"]:
        Image.new("RGB", (256, 256), "blue").save(folder / name)
        (folder / name).with_suffix(".txt").write_text("A blue square.", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    client = TestClient(
        create_app(Path(fixture["state"]), assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    )
    settings = client.get("/api/settings").json()
    assert (
        client.put(
            "/api/settings", json={**settings, "dataset_roots": [*settings["dataset_roots"], str(folder)]}
        ).status_code
        == 200
    )
    registration = client.post("/api/datasets", json={"name": "Preview and training formats", "path": str(folder)})
    assert registration.status_code == 200
    dataset_id = registration.json()["id"]
    items = client.get(f"/api/datasets/{dataset_id}/items").json()
    assert len(items) == 4
    assert all(item["valid"] for item in items)
    for item in items:
        assert client.get(f"/api/datasets/{dataset_id}/items/{item['id']}/media").status_code == 200
    created = client.post("/api/jobs", json={"name": "Image formats", "family": "wan", "dataset_id": dataset_id})
    assert created.status_code == 200
    job_id = created.json()["id"]
    review = client.post(f"/api/jobs/{job_id}/review", json={}).json()
    assert review["valid"] is False
    for name in ["animated.gif", "scan.tif", "scan2.tiff"]:
        assert any(name in error and "Convert" in error and "exclude" in error for error in review["errors"])
    assert not any("supported.png" in error for error in review["errors"])
    unsupported_ids = [item["id"] for item in items if item["name"] != "supported.png"]
    assert (
        client.post(
            f"/api/datasets/{dataset_id}/exclusions", json={"item_ids": unsupported_ids, "excluded": True}
        ).status_code
        == 200
    )
    accepted = client.post(f"/api/jobs/{job_id}/review", json={}).json()
    assert accepted["valid"] is True, accepted["errors"]
    assert accepted["effective_count"] == 1
    assert "image_directory = " in accepted["dataset_toml"]
    assert "video_directory = " not in accepted["dataset_toml"]
    assert all((folder / name).is_file() for name in ["animated.gif", "scan.tif", "scan2.tiff", "supported.png"])

from pathlib import Path

from test_datasets_api import PNG, fixture


def test_bulk_preview_selection_conflicts_exclusions_and_restore_are_scoped(tmp_path: Path) -> None:
    client, media, _ = fixture(tmp_path)
    (media / "blue.png").write_bytes(PNG)
    (media / "green.png").write_bytes(PNG)
    (media / "green.txt").write_text("leave unchanged", encoding="utf-8")
    dataset_id = client.post("/api/datasets", json={"name": "Bulk captions", "path": str(media)}).json()["id"]
    items = {item["name"]: item for item in client.get(f"/api/datasets/{dataset_id}/items").json()}
    selected = [items["red.png"]["id"], items["blue.png"]["id"]]
    preview = client.post(
        f"/api/datasets/{dataset_id}/bulk/preview",
        json={"item_ids": selected, "prefix": "trigger, ", "suffix": ".", "find": "red", "replace": "crimson"},
    )
    assert preview.status_code == 200, preview.text
    planned = preview.json()
    assert [item["after"] for item in planned["items"]] == ["trigger, A crimson square.", "trigger, ."]
    assert (media / "red.txt").read_text(encoding="utf-8") == "A red square"
    (media / "blue.txt").write_text("external blue", encoding="utf-8")
    operation = client.post(f"/api/datasets/{dataset_id}/bulk/apply", json={"preview_id": planned["id"]}).json()
    assert [item["status"] for item in operation["items"]] == ["saved", "conflict"]
    assert (media / "red.txt").read_text(encoding="utf-8") == "trigger, A crimson square."
    assert (media / "blue.txt").read_text(encoding="utf-8") == "external blue"
    assert (media / "green.txt").read_text(encoding="utf-8") == "leave unchanged"
    excluded = client.post(
        f"/api/datasets/{dataset_id}/exclusions", json={"item_ids": [items["blue.png"]["id"]], "excluded": True}
    )
    assert excluded.status_code == 200
    assert client.get(f"/api/datasets/{dataset_id}").json()["counts"]["included"] == 2
    restored = client.post(f"/api/datasets/{dataset_id}/bulk/restore", json={"operation_id": operation["id"]}).json()
    assert restored["items"][0]["status"] == "restored"
    assert (media / "red.txt").read_text(encoding="utf-8") == "A red square"
    assert (media / "blue.txt").read_text(encoding="utf-8") == "external blue"
    assert (media / "blue.png").read_bytes() == PNG

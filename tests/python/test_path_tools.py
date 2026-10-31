import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musubi_gui.app import create_app
from musubi_gui.path_tools import browse_path, check_path


def test_check_reports_each_state(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    file = folder / "model.safetensors"
    file.write_bytes(b"data")
    roots = [str(folder)]
    assert check_path("", "folder", [])["state"] == "empty"
    assert check_path(str(folder), "folder", [])["state"] == "ok"
    assert check_path(str(file), "file", roots)["state"] == "ok"
    assert check_path(str(tmp_path / "nope"), "folder", [])["state"] == "missing"
    assert check_path(str(file), "folder", [])["state"] == "wrong-type"
    assert check_path(str(folder), "file", [])["state"] == "wrong-type"
    assert check_path(str(tmp_path), "folder", roots)["state"] == "outside-root"
    assert check_path("relative/path", "folder", [])["state"] == "invalid"
    assert check_path("\\\\server\\share", "folder", [])["state"] == "invalid"
    assert check_path("https://example.invalid/x", "folder", [])["state"] == "invalid"


def test_check_and_browse_leave_junction_targets_outside_the_root_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "weights.safetensors").write_bytes(b"data")
    link = root / "link"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    assert check_path(str(link / "weights.safetensors"), "file", [str(root)])["state"] == "outside-root"


def test_browse_lists_names_folders_first_and_never_opens_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "b-folder").mkdir()
    (tmp_path / "A-folder").mkdir()
    (tmp_path / "secret.txt").write_text("contents", encoding="utf-8")

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("file contents must not be read")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "open", refuse)
    listed = browse_path(str(tmp_path), "file")
    assert [entry["name"] for entry in listed["entries"]] == ["A-folder", "b-folder", "secret.txt"]
    assert "contents" not in str(listed)
    assert listed["parent"] == str(tmp_path.parent)
    assert [entry["name"] for entry in browse_path(str(tmp_path), "folder")["entries"]] == ["A-folder", "b-folder"]


def test_browse_opens_the_closest_existing_folder_and_starts_at_the_drives(tmp_path: Path) -> None:
    assert browse_path(str(tmp_path / "not" / "there"), "folder")["path"] == str(tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    assert browse_path(str(file), "folder")["path"] == str(tmp_path)
    drives = browse_path("", "folder")
    assert drives["parent"] is None
    assert drives["entries"]
    assert Path(tmp_path.anchor).resolve() in [Path(entry["path"]).resolve() for entry in drives["entries"]]
    assert browse_path(tmp_path.anchor, "folder")["parent"] == ""


@pytest.mark.parametrize("value", ["\\\\server\\share", "https://example.invalid/x", "relative"])
def test_browse_refuses_network_and_relative_paths(value: str) -> None:
    with pytest.raises(ValueError):
        browse_path(value, "folder")


def test_routes_are_local_only(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    body = {"path": str(tmp_path), "kind": "folder"}
    assert client.post("/api/paths/check", json=body).json()["state"] == "ok"
    assert client.post("/api/paths/browse", json=body).status_code == 200
    for route in ["/api/paths/check", "/api/paths/browse"]:
        assert client.post(route, json=body, headers={"origin": "https://other.invalid"}).status_code == 403
        assert client.post(route, json=body, headers={"host": "other.invalid"}).status_code == 400
    refused = client.post("/api/paths/browse", json={"path": "\\\\server\\share"})
    assert refused.status_code == 422
    assert "Network paths" in refused.json()["detail"]

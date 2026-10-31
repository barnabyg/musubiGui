import json
from pathlib import Path

from fastapi.testclient import TestClient

from musubi_gui.app import create_app


def client_for(tmp_path: Path) -> TestClient:
    assets = tmp_path / "web"
    assets.mkdir(exist_ok=True)
    return TestClient(create_app(tmp_path / "state", assets))


def test_report_survives_restart_and_settings_changes_mark_it_out_of_date(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.get("/api/diagnostics").json() is None
    report = client.post("/api/diagnostics", json={}).json()
    assert report["checks"]
    assert report["current"] is True
    assert report["ran_at"]

    reopened = client_for(tmp_path).get("/api/diagnostics").json()
    assert reopened == report

    settings = client.get("/api/settings").json()
    changed = {**settings, "output_root": str(tmp_path / "output")}
    assert client.put("/api/settings", json=changed).status_code == 200
    stale = client.get("/api/diagnostics").json()
    assert stale["current"] is False
    assert stale["checks"] == report["checks"]
    assert client.put("/api/settings", json=settings).status_code == 200
    assert client.get("/api/diagnostics").json()["current"] is True


def test_report_is_versioned_apart_from_settings(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    settings = client.get("/api/settings").json()
    client.put("/api/settings", json=settings)
    before = (tmp_path / "state" / "settings.json").read_text(encoding="utf-8")
    client.post("/api/diagnostics", json={})
    assert (tmp_path / "state" / "settings.json").read_text(encoding="utf-8") == before
    assert json.loads((tmp_path / "state" / "diagnostics.json").read_text(encoding="utf-8"))["version"] == 1


def test_unreadable_or_future_reports_count_as_not_checked_and_settings_still_load(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    file = tmp_path / "state" / "diagnostics.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    for content in ["{not json", json.dumps({"version": 2, "checks": []}), "[]"]:
        file.write_text(content, encoding="utf-8")
        assert client.get("/api/diagnostics").json() is None
        assert client.get("/api/settings").status_code == 200
    assert client.post("/api/diagnostics", json={}).json()["current"] is True

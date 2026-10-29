from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musubi_gui.app import create_app
from musubi_gui.settings import Settings, load_settings, save_settings


def test_default_dataset_root_uses_project_not_state_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "separate-state"
    settings = load_settings(state)
    assert settings.dataset_roots == [str(tmp_path / "datasets")]
    save_settings(state, settings)
    assert load_settings(state).dataset_roots == settings.dataset_roots


@pytest.mark.parametrize("roots", [[], ["custom"]])
def test_saved_dataset_roots_are_preserved(tmp_path: Path, roots: list[str]) -> None:
    configured = [str(tmp_path / value) for value in roots]
    save_settings(tmp_path, Settings(dataset_roots=configured))
    assert load_settings(tmp_path).dataset_roots == configured


def test_browser_cannot_change_settings_from_another_origin(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    response = client.put("/api/settings", json={}, headers={"origin": "https://other.invalid"})
    assert response.status_code == 403


def test_reject_model_escape_and_preserve_last_saved_settings(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    initial = client.get("/api/settings").json()
    saved = {**initial, "output_root": str(tmp_path / "output"), "cache_root": str(tmp_path / "cache")}
    assert client.put("/api/settings", json=saved).status_code == 200
    escaping = {
        **saved,
        "model_roots": [str(tmp_path / "models")],
        "models": {"wan_low": str(tmp_path / "private.safetensors")},
    }
    assert client.put("/api/settings", json=escaping).status_code == 422
    restarted = TestClient(create_app(tmp_path / "state", assets))
    assert restarted.get("/api/settings").json() == saved


@pytest.mark.parametrize("payload", ['{"version": 7}', "[]", "null"])
def test_unreadable_persisted_settings_are_not_overwritten(tmp_path: Path, payload: str) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "settings.json").write_text(payload, encoding="utf-8")
    client = TestClient(create_app(state, assets))
    assert client.get("/api/settings").status_code == 409
    assert client.put("/api/settings", json={}).status_code == 409


def test_partial_setup_cannot_write_inside_training_checkout(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    settings = client.get("/api/settings").json()
    settings.update(checkout=str(tmp_path / "upstream"), output_root=str(tmp_path / "upstream/generated"))
    assert client.put("/api/settings", json=settings).status_code == 422


def test_root_lists_drop_blank_lines_and_surrounding_whitespace(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    models, datasets, media = (str(tmp_path / name) for name in ["models", "datasets", "media"])
    settings = client.get("/api/settings").json()
    settings.update(
        model_roots=[f"  {models}\t", "", "   "],
        dataset_roots=["", f" {datasets}", "\t", f"{media}  "],
    )
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200
    expected = {"model_roots": [models], "dataset_roots": [datasets, media]}
    assert {key: response.json()[key] for key in expected} == expected
    restarted = TestClient(create_app(tmp_path / "state", assets))
    assert {key: restarted.get("/api/settings").json()[key] for key in expected} == expected


def test_diagnostics_warn_about_each_root_that_is_not_an_existing_directory(tmp_path: Path) -> None:
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(tmp_path / "state", assets))
    present = tmp_path / "present"
    present.mkdir()
    not_folder = tmp_path / "weights.safetensors"
    not_folder.write_bytes(b"")
    missing = tmp_path / "missing models"
    joined = f"{tmp_path / 'datasets'}{tmp_path / 'sample_vids'}"
    settings = client.get("/api/settings").json()
    settings.update(model_roots=[str(present), str(missing), str(not_folder)], dataset_roots=[joined, str(present)])
    assert client.put("/api/settings", json=settings).status_code == 200
    checks = {check["name"]: check for check in client.post("/api/diagnostics", json={}).json()["checks"]}
    assert checks["Model roots"]["status"] == "warning"
    assert checks["Model roots"]["message"].count(str(tmp_path)) == 2
    assert str(missing) in checks["Model roots"]["message"]
    assert str(not_folder) in checks["Model roots"]["message"]
    assert checks["Dataset roots"]["status"] == "warning"
    assert checks["Dataset roots"]["message"].count(str(tmp_path)) == 2  # one joined entry, not the present root
    assert joined in checks["Dataset roots"]["message"]
    settings.update(model_roots=[str(present)], dataset_roots=[str(present)])
    assert client.put("/api/settings", json=settings).status_code == 200
    names = {check["name"] for check in client.post("/api/diagnostics", json={}).json()["checks"]}
    assert not names & {"Model roots", "Dataset roots"}

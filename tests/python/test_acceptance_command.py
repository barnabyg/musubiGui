import json
import subprocess
import sys
from pathlib import Path


def test_acceptance_refuses_existing_workspace_without_changing_its_files(tmp_path: Path) -> None:
    workspace = tmp_path / "existing workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("keep my data", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.acceptance", "--workspace", str(workspace), "--prepare-only"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 2
    assert "new, nonexistent directory" in result.stdout
    assert marker.read_text(encoding="utf-8") == "keep my data"
    assert sorted(path.name for path in workspace.iterdir()) == ["keep.txt"]


def test_acceptance_prepares_two_models_without_starting_training(tmp_path: Path) -> None:
    workspace = tmp_path / "新 acceptance workspace"
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.acceptance", "--workspace", str(workspace), "--prepare-only"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((workspace / "acceptance.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "fixture"
    assert manifest["gpu_evidence"] is False
    assert len(manifest["jobs"]) == 4
    assert {job["family"] for job in manifest["jobs"]} == {"wan", "h3"}
    settings = json.loads((workspace / "state" / "settings.json").read_text(encoding="utf-8"))
    assert Path(settings["checkout"]).is_relative_to(workspace)
    assert Path(settings["python"]) == Path(sys.executable)
    marker = json.loads((Path(settings["checkout"]) / ".musubi-gui-fixture.json").read_text(encoding="utf-8"))
    assert marker == {"version": 1, "kind": "synthetic-worker"}
    assert not list((workspace / "outputs").glob("**/*.safetensors"))

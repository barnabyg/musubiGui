import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from musubi_gui.app_update import AppUpdate
from musubi_gui.launcher import read_update
from musubi_gui.managed_checkout import ManagedCheckout
from musubi_gui.processes import process_identity
from musubi_gui.run_store import DeviceLease, RunStore


@pytest.mark.skipif(os.name != "nt", reason="Uses the Windows batch launcher.")
def test_windows_launcher_uses_system_python_and_preserves_options(tmp_path: Path) -> None:
    project = tmp_path / "checkout with spaces"
    package = project / "musubi_gui"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "launcher.py").write_text(
        "import sys\nassert sys.prefix == sys.base_prefix\nassert sys.argv[1:] == ['--port', '8769', '--no-browser']\n",
        encoding="utf-8",
    )
    shutil.copyfile(Path(__file__).parents[2] / "launch.cmd", project / "launch.cmd")
    shell = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
    result = subprocess.run(
        [str(shell), "/d", "/c", "launch.cmd", "--port", "8769", "--no-browser"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_queued_run_blocks_application_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state"
    device = DeviceLease(state, tmp_path / "device.sqlite3")
    RunStore(state).create({"id": "queued", "fingerprint": "fixture", "status": "pending_training"})
    restart = Mock()
    update = AppUpdate(ManagedCheckout(state, device), restart)
    update.controller = process_identity()

    with pytest.raises(ValueError, match="Finish or cancel queued and active Runs"):
        update.start()

    restart.assert_not_called()
    assert read_update().phase == "idle"
    assert device.owner() is None

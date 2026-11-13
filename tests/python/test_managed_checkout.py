import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import wait_for

import musubi_gui.managed_checkout as managed
from musubi_gui.app import create_app
from musubi_gui.managed_checkout import ManagedCheckout, checkout_gate
from musubi_gui.recipe import Stage
from musubi_gui.run_store import DeviceLease, RunStore
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings, load_settings, save_settings
from musubi_gui.snapshots import checkout_identity


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(
        [shutil.which("git") or "git", "-C", str(directory), *args], text=True, encoding="utf-8"
    ).strip()


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ManagedCheckout, Path, str]:
    project = tmp_path / "project with spaces 日本語"
    project.mkdir()
    monkeypatch.chdir(project)
    remote = tmp_path / "upstream"
    remote.mkdir()
    git(remote, "init")
    for name in ["pyproject.toml", "wan_train_network.py", "minimax_h3_train_network.py"]:
        (remote / name).write_text("# synthetic fixture", encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "fixture")
    revision = git(remote, "rev-parse", "HEAD")
    monkeypatch.setattr(managed, "REMOTE", str(remote))
    monkeypatch.setattr(managed, "UPSTREAM", revision)
    device = DeviceLease(project / "state", tmp_path / "device.sqlite3")
    manager = ManagedCheckout(project / "state", device)
    original = manager.command

    def command(args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
        if args[1] in {"venv", "pip"}:
            manager.python.parent.mkdir(parents=True, exist_ok=True)
            manager.python.write_text("synthetic python", encoding="utf-8")
            return ""
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(manager, "command", command)
    return manager, remote, revision


def wait(manager: ManagedCheckout) -> dict[str, object]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = manager.status()
        if status["operation"]["state"] != "running" and not manager.lock.locked():
            return status
        time.sleep(0.02)
    pytest.fail("Setup did not finish")


def test_installs_supported_pin_not_remote_head_and_updates_preserving_previous(
    checkout: tuple[ManagedCheckout, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, remote, revision = checkout
    (remote / "new.py").write_text("# next supported revision", encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "new")
    newer = git(remote, "rev-parse", "HEAD")
    manager.start()
    assert wait(manager)["ready"] is True
    assert git(manager.checkout, "rev-parse", "HEAD") == revision
    monkeypatch.setattr(managed, "UPSTREAM", newer)
    manager.start()
    assert wait(manager)["ready"] is True
    assert git(manager.checkout, "rev-parse", "HEAD") == newer
    assert git(manager.checkout, "rev-parse", "refs/musubi-gui/previous") == revision
    assert manager.device.owner() is None
    restarted = ManagedCheckout(manager.state, manager.device)
    assert restarted.status()["ready"] is True


def test_dirty_source_is_never_overwritten(checkout: tuple[ManagedCheckout, Path, str]) -> None:
    manager, _, revision = checkout
    manager.install()
    script = manager.checkout / "wan_train_network.py"
    script.write_text("# local edit", encoding="utf-8")
    manager.start()
    assert wait(manager)["operation"]["state"] == "failed"
    assert "local source changes" in manager.status()["operation"]["message"]
    assert script.read_text(encoding="utf-8") == "# local edit"
    assert git(manager.checkout, "rev-parse", "HEAD") == revision
    assert manager.device.owner() is None


def test_foreign_directory_and_origin_are_preserved(checkout: tuple[ManagedCheckout, Path, str]) -> None:
    manager, _, _ = checkout
    manager.checkout.mkdir()
    sentinel = manager.checkout / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="not a managed Git checkout"):
        manager.install()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    sentinel.unlink()
    manager.checkout.rmdir()
    manager.install()
    git(manager.checkout, "remote", "set-url", "origin", "https://example.invalid/foreign.git")
    with pytest.raises(ValueError, match="unexpected origin"):
        manager.install()


def test_setup_respects_gpu_ownership_and_snapshot_gate(checkout: tuple[ManagedCheckout, Path, str]) -> None:
    manager, _, _ = checkout
    assert manager.device.acquire("training-run", {})
    with pytest.raises(ValueError, match="owns the GPU"):
        manager.start()
    assert manager.device.owner()["run_id"] == "training-run"
    manager.device.release("training-run")
    with checkout_gate(), pytest.raises(ValueError, match="already running"):
        manager.start()
    manager.start()
    assert wait(manager)["ready"] is True


def test_queued_run_blocks_update_before_any_source_is_changed(checkout: tuple[ManagedCheckout, Path, str]) -> None:
    manager, _, _ = checkout
    RunStore(manager.state).create({"id": "queued", "fingerprint": "fixture", "status": "pending_preparation"})
    with pytest.raises(ValueError, match="queued and active"):
        manager.start()
    assert not manager.checkout.exists()
    assert manager.device.owner() is None


def test_status_does_not_report_idle_during_windows_file_replacement(
    checkout: tuple[ManagedCheckout, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from musubi_gui.processes import process_identity

    manager, _, _ = checkout
    manager.write_status({"state": "running", "message": "Installing", "controller": process_identity()})
    original = Path.read_text

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == manager.record:
            raise PermissionError("Synthetic Windows sharing violation")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert manager.status()["operation"]["state"] == "running"


def test_dependency_failure_is_retryable_and_never_reports_ready(
    checkout: tuple[ManagedCheckout, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _, _ = checkout
    original = manager.command

    def fail(args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
        if args[1] == "pip":
            raise ValueError("dependency download failed")
        return original(args, cwd=cwd, timeout=timeout)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "command", fail)
        manager.start()
        result = wait(manager)
        assert result["ready"] is False
        assert result["operation"]["state"] == "failed"
    manager.start()
    assert wait(manager)["ready"] is True


def test_installer_pins_triton_and_invalidates_pre_acceleration_readiness(
    checkout: tuple[ManagedCheckout, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _, revision = checkout
    commands: list[list[str]] = []
    original = manager.command

    def capture(args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
        commands.append(args)
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(manager, "command", capture)
    manager.install()
    requirement = f"triton-windows=={managed.TRITON_WINDOWS}"
    installs = [args for args in commands if requirement in args]
    assert len(installs) == 1
    assert installs[0][installs[0].index("--python") + 1] == str(manager.python)
    assert installs[0][installs[0].index("--only-binary") + 1] == ":all:"
    assert requirement in (manager.directory / "constraints.txt").read_text(encoding="utf-8")
    assert manager.status()["ready"]
    # Released readiness markers remain readable, but require the updated environment.
    (manager.directory / "ready").write_text(
        f"{revision}\ncu130\n{managed.TORCH}\n{managed.TORCHVISION}", encoding="utf-8"
    )
    assert not manager.status()["ready"]


def test_triton_install_failure_keeps_setup_retryable_without_a_ready_marker(
    checkout: tuple[ManagedCheckout, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _, _ = checkout
    original = manager.command

    def fail_triton(args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
        if f"triton-windows=={managed.TRITON_WINDOWS}" in args:
            raise ValueError("Triton wheel download failed")
        return original(args, cwd=cwd, timeout=timeout)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "command", fail_triton)
        manager.start()
        status = wait(manager)
        assert status["operation"]["state"] == "failed"
        assert not status["ready"]
        assert not (manager.directory / "ready").exists()
        assert manager.device.owner() is None
    manager.start()
    assert wait(manager)["ready"]


def test_api_migrates_paths_without_touching_external_install_or_other_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / "sentinel").touch()
    state = tmp_path / "state"
    old = Settings(
        checkout=str(external),
        python=str(external / "python.exe"),
        models={"wan_low": ""},
        dataset_roots=[str(tmp_path / "data")],
    )
    save_settings(state, old)
    assets = tmp_path / "web"
    assets.mkdir()
    client = TestClient(create_app(state, assets, device_path=tmp_path / "gpu.sqlite3"))
    settings = client.get("/api/settings").json()
    assert settings["checkout"] == str(tmp_path / "musubi-tuner")
    assert settings["dataset_roots"] == old.dataset_roots
    assert load_settings(state) == old  # Reads do not rewrite the persisted compatibility contract.
    assert client.put("/api/settings", json=old.model_dump()).json() == settings
    assert (external / "sentinel").exists()
    assert json.loads((state / "settings.json").read_text(encoding="utf-8"))["version"] == 1
    assert client.post("/api/checkout/update", json={}, headers={"origin": "https://other.invalid"}).status_code == 403
    assert client.get("/api/checkout").json()["ready"] is False


def test_previous_external_snapshot_is_readable_but_cannot_execute(checkout: tuple[ManagedCheckout, Path, str]) -> None:
    manager, external, _ = checkout
    marker = external / "must-not-run"
    settings = Settings(checkout=str(external), python=sys.executable)
    service = RunService(manager.state, device_path=manager.device.path)
    try:
        run = service.submit(
            ExecutionPlan(
                job={"id": "old", "revision": 1, "family": "wan"},
                settings=settings,
                manifest={},
                directory=external,
                cache=external,
                fingerprint="old-external",
                stages=[
                    Stage(
                        "latent_cache",
                        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                    )
                ],
                provenance={"checkout": checkout_identity(external)},
            )
        )
        failed = wait_for(service, run["id"], "failed", device_released=True)
        assert "Prepare a new Run with the managed checkout" in failed["error"]
        assert failed["settings"] == settings.model_dump()
        assert not marker.exists()
    finally:
        service.close()

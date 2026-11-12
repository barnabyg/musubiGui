"""Project-owned Musubi source and environment, updated only to the GUI's supported pin."""

import os
import shutil
import sqlite3
import subprocess  # nosec B404
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException

from musubi_gui.diagnostics import upstream_revision
from musubi_gui.processes import identity_status, observed_tree, process_identity
from musubi_gui.recipe import UPSTREAM as UPSTREAM
from musubi_gui.redaction import redact
from musubi_gui.run_store import DeviceLease, RunStore, is_active
from musubi_gui.settings import Settings, atomic_json, managed_settings

REMOTE = "https://github.com/kohya-ss/musubi-tuner.git"
TORCH = "2.13.0"
TORCHVISION = "0.28.0"


def supported_environment() -> str:
    return f"{UPSTREAM}\ncu130\n{TORCH}\n{TORCHVISION}"


@contextmanager
def checkout_gate() -> Iterator[None]:
    """Shared by update and snapshot creation, including backends with different state roots."""
    directory = Path.cwd() / ".cache"
    directory.mkdir(exist_ok=True)
    with closing(
        sqlite3.connect(directory / "checkout-lock.sqlite3", timeout=0, isolation_level=None, check_same_thread=False)
    ) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise ValueError("Musubi setup or preparation is already running. Wait for it to finish.") from error
        try:
            yield
        finally:
            connection.rollback()


class ManagedCheckout:
    def __init__(self, state: Path, device: DeviceLease):
        self.state = state
        self.device = device
        settings = managed_settings(Settings())
        self.checkout = Path(settings.checkout)
        self.python = Path(settings.python)
        self.directory = Path.cwd() / ".cache" / "musubi-setup"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.record = self.directory / "status.json"
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.last_operation: dict[str, Any] = {
            "state": "idle",
            "message": "Install the supported Musubi version to get started.",
        }

    def write_status(self, operation: dict[str, Any]) -> None:
        # A different Windows backend may briefly hold the previous file open while polling.
        with self.status_lock:
            for attempt in range(20):
                try:
                    atomic_json(self.record, operation)
                    self.last_operation = operation
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)

    def command(self, args: list[str], *, cwd: Path | None = None, timeout: int = 900) -> str:
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT="0", UV_NO_PROGRESS="1", UV_PROJECT_ENVIRONMENT=str(self.checkout / ".venv"))
        # B603: fixed Git/uv arrays below, no shell or API-supplied commands. Revisit if sources become selectable.
        with subprocess.Popen(  # nosec B603
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            self.device.update("managed-checkout", observed_tree(process.pid))
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                process.communicate()
                raise ValueError(
                    "Musubi setup timed out. Retry setup; existing datasets and models are preserved."
                ) from error
        if process.returncode:
            (self.directory / "error.log").write_text(redact(output), encoding="utf-8")
            raise ValueError(f"Musubi setup command failed. See {self.directory / 'error.log'} and retry setup.")
        return output.strip()

    def git(self, *args: str) -> str:
        git = shutil.which("git")
        if not git:
            raise ValueError("Install Git for Windows, then retry setup.")
        return self.command([git, "-c", "core.hooksPath=", "-C", str(self.checkout), *args])

    def status(self) -> dict[str, Any]:
        import json

        with self.status_lock:
            try:
                operation = json.loads(self.record.read_text(encoding="utf-8"))
                if not isinstance(operation, dict) or not isinstance(operation.get("state"), str):
                    raise ValueError("Unreadable setup status")
                self.last_operation = operation
            except (OSError, ValueError):
                operation = self.last_operation.copy()
        if operation.get("state") == "running" and identity_status(operation.get("controller", {})) == "dead":
            operation = {"state": "failed", "message": "Setup was interrupted. Retry setup to finish."}
        revision = ""
        if (self.checkout / ".git").is_dir():
            try:
                revision = upstream_revision(self.checkout)
            except (ValueError, OSError, subprocess.TimeoutExpired):
                pass
        try:
            ready = (self.directory / "ready").read_text(encoding="utf-8") == supported_environment()
        except OSError:
            ready = False
        return {
            "checkout": str(self.checkout),
            "python": str(self.python),
            "supported_revision": UPSTREAM,
            "installed_revision": revision,
            "ready": ready and revision == UPSTREAM and self.python.is_file(),
            "operation": operation,
        }

    def start(self) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            raise ValueError("Musubi setup is already running.")
        # Reserve synchronously, so another request can't prepare a snapshot before the thread starts.
        gate = checkout_gate()
        acquired = False
        leased = False
        try:
            gate.__enter__()
            acquired = True
            if any(is_active(run) for run in RunStore(self.state).list()):
                raise ValueError("Finish or cancel queued and active Runs before updating Musubi.")
            owner = self.device.owner()
            if (
                owner
                and owner["run_id"] == "managed-checkout"
                and identity_status(owner["controller"]) == "dead"
                and all(identity_status(p) == "dead" for p in owner.get("processes", []))
            ):
                DeviceLease(Path(owner["state"]), self.device.path).release("managed-checkout")
            if not self.device.acquire("managed-checkout", process_identity()):
                raise ValueError("Training or another setup operation owns the GPU. Wait before updating Musubi.")
            leased = True
            self.write_status(
                {
                    "state": "running",
                    "message": "Installing supported Musubi source and dependencies…",
                    "controller": process_identity(),
                },
            )
        except BaseException:
            if leased:
                self.device.release("managed-checkout")
            if acquired:
                gate.__exit__(None, None, None)
            self.lock.release()
            raise

        def work() -> None:
            try:
                (self.state / "diagnostics.json").unlink(missing_ok=True)
                self.install()
                self.write_status(
                    {
                        "state": "complete",
                        "message": "Supported Musubi version installed. Run diagnostics before training.",
                    },
                )
            except (ValueError, OSError, psutil.Error) as error:
                self.write_status({"state": "failed", "message": redact(str(error))})
            finally:
                self.device.release("managed-checkout")
                gate.__exit__(None, None, None)
                self.lock.release()

        # check_same_thread=False permits handing the reserved gate to this worker; it has one owner at a time.
        worker = threading.Thread(target=work, name="musubi-setup", daemon=True)
        worker.start()
        return self.status()

    def install(self) -> None:
        uv = shutil.which("uv")
        git = shutil.which("git")
        if not uv or not git:
            raise ValueError("Install uv and Git for Windows, then retry setup.")
        if self.checkout.is_symlink() or self.checkout.is_junction():
            raise ValueError("The managed musubi-tuner folder must be a local directory, not a link or junction.")
        if not self.checkout.exists():
            self.command([git, "clone", "--no-checkout", REMOTE, str(self.checkout)])
        if (
            not (self.checkout / ".git").is_dir()
            or (self.checkout / ".git").is_symlink()
            or (self.checkout / ".git").is_junction()
        ):
            raise ValueError("musubi-tuner already exists but is not a managed Git checkout. Move it aside and retry.")
        if self.git("remote", "get-url", "origin") != REMOTE:
            raise ValueError(
                "The managed checkout has an unexpected origin. Restore the official origin before updating."
            )
        # uv generates a local lock and environment; neither is an upstream source modification.
        exclude = self.checkout / ".git" / "info" / "exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.venv/\n/uv.lock\n")
        head = self.git("rev-parse", "--verify", "HEAD")
        # A new --no-checkout clone reports every file as deleted until its first checkout.
        initial = not (self.checkout / "pyproject.toml").exists() and not self.git("ls-files")
        if not initial and self.git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError(
                "Musubi has local source changes. Commit or move them aside before updating; nothing was overwritten."
            )
        self.git("fetch", "--no-tags", REMOTE, UPSTREAM)
        self.git("update-ref", "refs/musubi-gui/previous", head)
        self.git("checkout", "--detach", UPSTREAM)
        (self.directory / "ready").unlink(missing_ok=True)
        if not self.python.is_file():
            self.command([uv, "venv", "--python", "3.12", str(self.checkout / ".venv")], timeout=300)
        # Resolve only this Windows environment. Universal `uv sync` resolves upstream's other CUDA
        # extras too, preventing a constraint on our qualified CUDA 13.0 versions.
        constraints = self.directory / "constraints.txt"
        constraints.write_text(f"torch=={TORCH}\ntorchvision=={TORCHVISION}\n", encoding="utf-8")
        self.command(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(self.python),
                "--default-index",
                "https://download.pytorch.org/whl/cu130",
                f"torch=={TORCH}",
                f"torchvision=={TORCHVISION}",
            ],
            cwd=self.checkout,
            timeout=3600,
        )
        self.command(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(self.python),
                "--editable",
                ".",
                "--group",
                "dev",
                "--constraints",
                str(constraints),
            ],
            cwd=self.checkout,
            timeout=3600,
        )
        (self.directory / "ready").write_text(supported_environment(), encoding="utf-8")


def checkout_router(manager: ManagedCheckout) -> APIRouter:
    router = APIRouter(prefix="/api/checkout")

    @router.get("")
    def status() -> dict[str, Any]:
        return manager.status()

    @router.post("/update")
    def update() -> dict[str, Any]:
        try:
            return manager.start()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "Cannot write setup state. Check project permissions and free disk.") from error

    return router

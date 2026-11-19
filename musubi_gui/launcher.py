"""Supervise the Windows app from outside its replaceable Python environment."""

import json
import os
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

ORIGIN = "https://github.com/barnabyg/musubiGui.git"
BRANCH = "refs/heads/main"
RESTART = 75
LAUNCHER_PID = "MUSUBI_GUI_LAUNCHER_PID"
UPDATE_STATUS = Path(".cache/gui-update.sqlite3")
RUNTIME_LOCK = Path(".cache/gui-runtime.sqlite3")


@dataclass(frozen=True)
class UpdateStatus:
    phase: str = "idle"
    message: str = ""
    controller: dict[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.phase in {"restart", "rebuild", "setup", "diagnostics"}


def read_update() -> UpdateStatus:
    if not UPDATE_STATUS.exists():
        return UpdateStatus()
    with closing(sqlite3.connect(UPDATE_STATUS)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS operation (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        row = db.execute("SELECT status FROM operation WHERE id=1").fetchone()
        return UpdateStatus(**json.loads(row[0])) if row else UpdateStatus()


def write_update(status: UpdateStatus) -> None:
    UPDATE_STATUS.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(UPDATE_STATUS, isolation_level=None)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS operation (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        db.execute("REPLACE INTO operation VALUES (1, ?)", (json.dumps(asdict(status)),))


def command(args: list[str], *, capture: bool = False, timeout: int = 1200) -> str:
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    # B603: fixed local build and Git commands; API requests supply no arguments.
    with subprocess.Popen(  # nosec B603
        args,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if process.poll() is None:
                taskkill = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
                try:
                    # B603: stop only the still-open child process's tree on Windows.
                    subprocess.run(  # nosec B603
                        [str(taskkill), "/T", "/F", "/PID", str(process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=30,
                    )
                finally:
                    process.kill()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)
        return (output or "").strip()


def git(*args: str, timeout: int = 30) -> str:
    executable = shutil.which("git")
    if not executable:
        raise ValueError("Install Git for Windows and retry the update.")
    return command([executable, "-c", "core.hooksPath=", *args], capture=True, timeout=timeout)


def check_checkout() -> str:
    try:
        root = Path(git("rev-parse", "--show-toplevel")).resolve()
        origin = git("config", "--get", "remote.origin.url")
        branch = git("rev-parse", "--symbolic-full-name", "HEAD")
    except subprocess.SubprocessError as error:
        raise ValueError("The updater requires a Git clone of Musubi GUI.") from error
    if root != Path.cwd() or origin not in {
        ORIGIN,
        ORIGIN.removesuffix(".git"),
        "git@github.com:barnabyg/musubiGui.git",
        "ssh://git@github.com/barnabyg/musubiGui.git",
    }:
        raise ValueError("The updater requires the official Musubi GUI repository as origin.")
    if branch != BRANCH:
        raise ValueError("Switch the GUI checkout to main before updating.")
    try:
        if git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError("The GUI checkout has local changes. Commit or move them aside before updating.")
        return git("rev-parse", "HEAD")
    except subprocess.SubprocessError as error:
        raise ValueError("Could not inspect the GUI checkout. Check Git and retry.") from error


def update_gui() -> bool:
    before = check_checkout()
    try:
        git("fetch", "--no-tags", ORIGIN, f"+{BRANCH}:refs/remotes/origin/main", timeout=300)
        target = git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        ancestor = git("merge-base", before, target)
    except subprocess.SubprocessError as error:
        raise ValueError("Could not fetch or verify Musubi GUI main. Check the Git connection and checkout.") from error
    if ancestor != before:
        raise ValueError("The local main branch has commits outside upstream main. Reconcile it before updating.")
    if check_checkout() != before:
        raise ValueError("The GUI checkout changed during the update. Try again.")
    if target == before:
        return False
    try:
        git("merge", "--ff-only", "--no-overwrite-ignore", "--no-autostash", target)
    except subprocess.SubprocessError as error:
        raise ValueError("Could not fast-forward the GUI safely. Check the checkout and retry.") from error
    return True


def prepare_gui() -> None:
    uv, npm = shutil.which("uv"), shutil.which("npm.cmd")
    if not uv or not npm:
        raise ValueError("Install uv and Node.js/npm before launching Musubi GUI.")
    command([uv, "sync", "--locked"])
    command([npm, "ci", "--cache", ".cache/npm"])
    command([npm, "run", "build"])


def serve(args: list[str], env: dict[str, str]) -> int:
    python = str(Path(".venv/Scripts/python.exe").resolve())
    # B603: fixed application Python and local launcher arguments; no shell.
    return subprocess.run([python, "-m", "musubi_gui", *args], env=env).returncode  # nosec B603


def share_runtime(lock: sqlite3.Connection) -> None:
    # A rollback-journal read transaction keeps an exclusive updater out while this instance serves.
    lock.execute("BEGIN")
    lock.execute("SELECT name FROM sqlite_master").fetchall()


def restart_gui(lock: sqlite3.Connection, status: UpdateStatus) -> None:
    lock.rollback()
    try:
        lock.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError:
        write_update(
            replace(status, phase="failed", message="Close other Musubi GUI instances using this checkout and retry.")
        )
        return
    try:
        try:
            changed = update_gui()
        except ValueError as error:
            write_update(replace(status, phase="failed", message=str(error)))
            return
        message = "Rebuilding and restarting Musubi GUI…" if changed else "Restarting Musubi GUI…"
        write_update(replace(status, phase="rebuild", message=message))
        if changed:
            try:
                prepare_gui()
            except (OSError, subprocess.SubprocessError, ValueError):
                write_update(
                    replace(
                        status,
                        phase="failed",
                        message="The GUI update was downloaded, but its build failed. See the launcher output and relaunch.",
                    )
                )
                raise
    finally:
        lock.rollback()


def launch(args: list[str]) -> int:
    RUNTIME_LOCK.parent.mkdir(exist_ok=True)
    env = os.environ | {LAUNCHER_PID: str(os.getpid())}
    with closing(sqlite3.connect(RUNTIME_LOCK, isolation_level=None)) as lock:
        share_runtime(lock)
        prepare_gui()
        code = serve(args, env)
        while code == RESTART:
            status = read_update()
            if status.phase != "restart" or status.controller.get("pid") != os.getpid():
                raise ValueError("The launcher could not find its pending GUI update. Relaunch Musubi GUI.")
            restart_gui(lock, status)
            share_runtime(lock)
            # The existing tab reconnects on the original port. Keep all other launch options.
            code = serve(args if "--no-browser" in args else [*args, "--no-browser"], env)
        return code


if __name__ == "__main__":
    try:
        sys.exit(launch(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError) as error:
        sys.exit(f"Musubi GUI launcher: {error}")

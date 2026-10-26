"""Observed process identities; a PID alone is never ownership evidence."""

import os
import signal
from typing import Any

import psutil


def process_identity(pid: int | None = None) -> dict[str, Any]:
    process = psutil.Process(pid or os.getpid())
    return {"pid": process.pid, "created": process.create_time(), "exe": process.exe()}


def matching_process(identity: dict[str, Any]) -> psutil.Process | None:
    try:
        process = psutil.Process(int(identity["pid"]))
        if abs(process.create_time() - float(identity["created"])) > 0.01:
            return None
        if os.path.normcase(process.exe()) != os.path.normcase(str(identity["exe"])):
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except psutil.NoSuchProcess:
        return None


def observed_tree(pid: int) -> list[dict[str, Any]]:
    try:
        parent = psutil.Process(pid)
        identities = []
        for process in [parent, *parent.children(recursive=True)]:
            try:
                identities.append(process_identity(process.pid))
            except psutil.NoSuchProcess:
                pass
        return identities
    except psutil.NoSuchProcess:
        return []


def terminate_owned(identities: list[dict[str, Any]], force: bool) -> None:
    processes = []
    for identity in identities:
        process = matching_process(identity)
        if process:
            processes.append(process)
    for process in reversed(processes):
        try:
            if force:
                process.kill()
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except psutil.NoSuchProcess:
            pass

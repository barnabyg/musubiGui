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
    """Kill every owned process, or ask them to stop gracefully.

    A Windows console break is addressed to a process group. The first identity is the stage
    launcher, created as a group leader, and its descendants inherit that group, so a graceful
    stop signals only the launcher. Breaking any other PID is undefined and can reach every
    process on the console, including this one. Once the launcher has exited there is no group
    leader left to signal, and remaining descendants need a confirmed force stop.
    """
    processes = []
    for identity in identities:
        process = matching_process(identity)
        if process:
            processes.append(process)
    if not force and os.name == "nt":
        processes = processes[:1] if processes and processes[0].pid == identities[0]["pid"] else []
    for process in reversed(processes):
        try:
            if force:
                process.kill()
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        except psutil.NoSuchProcess:
            pass


def identity_status(identity: dict[str, Any]) -> str:
    try:
        return "alive" if matching_process(identity) else "dead"
    except (psutil.AccessDenied, OSError, KeyError, ValueError):
        return "uncertain"

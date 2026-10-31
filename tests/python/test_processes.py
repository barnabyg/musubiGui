import signal
from typing import Any

import pytest

from musubi_gui import processes


class FakeProcess:
    def __init__(self, pid: int, calls: list[tuple[int, str]]):
        self.pid = pid
        self.calls = calls

    def send_signal(self, value: int) -> None:
        self.calls.append((self.pid, "break" if value == signal.CTRL_BREAK_EVENT else str(value)))

    def kill(self) -> None:
        self.calls.append((self.pid, "kill"))


def owned(monkeypatch: pytest.MonkeyPatch, alive: set[int]) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    calls: list[tuple[int, str]] = []
    identities = [{"pid": pid, "created": 0.0, "exe": "python.exe"} for pid in (10, 11, 12)]
    monkeypatch.setattr(
        processes,
        "matching_process",
        lambda identity: FakeProcess(identity["pid"], calls) if identity["pid"] in alive else None,
    )
    return identities, calls


def test_graceful_stop_breaks_only_the_launcher_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    identities, calls = owned(monkeypatch, {10, 11, 12})
    processes.terminate_owned(identities, force=False)
    assert calls == [(10, "break")]


def test_graceful_stop_never_breaks_descendants_after_the_launcher_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    identities, calls = owned(monkeypatch, {11, 12})
    processes.terminate_owned(identities, force=False)
    assert calls == []


def test_force_stop_kills_every_owned_process_children_first(monkeypatch: pytest.MonkeyPatch) -> None:
    identities, calls = owned(monkeypatch, {10, 11, 12})
    processes.terminate_owned(identities, force=True)
    assert calls == [(12, "kill"), (11, "kill"), (10, "kill")]

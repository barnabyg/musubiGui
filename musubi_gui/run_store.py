"""Transactional run history and host-wide, durable GPU ownership."""

import builtins
import json
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Terminal statuses for runs that ended without completing; they never block a fresh run of the same revision.
ENDED_EARLY = frozenset({"failed", "cancelled", "stopped", "interrupted"})


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, 1}:
            raise ValueError("Unsupported run database version. Preserve it and use a compatible application.")
        connection.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        connection.execute("PRAGMA user_version=1")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


class RunStore:
    def __init__(self, root: Path):
        self.path = root / "runs.sqlite3"

    def list(self) -> list[dict[str, Any]]:
        with database(self.path) as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT data FROM records ORDER BY rowid DESC")]

    def get(self, run_id: str) -> dict[str, Any]:
        with database(self.path) as connection:
            row = connection.execute("SELECT data FROM records WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Run not found.")
            return dict(json.loads(row[0]))

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        with database(self.path) as connection:
            for row in connection.execute("SELECT data FROM records"):
                previous = json.loads(row[0])
                if previous["fingerprint"] == data["fingerprint"] and previous["status"] not in ENDED_EARLY:
                    return dict(previous)
            connection.execute("INSERT INTO records VALUES (?,?)", (data["id"], json.dumps(data)))
            return data

    def update(self, run_id: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with database(self.path) as connection:
            row = connection.execute("SELECT data FROM records WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Run not found.")
            data: dict[str, Any] = json.loads(row[0])
            change(data)
            connection.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(data), run_id))
            return data

    def reorder(self, run_ids: builtins.list[str]) -> None:
        with database(self.path) as connection:
            records = [json.loads(row[0]) for row in connection.execute("SELECT data FROM records")]
            pending = {
                record["id"]: record
                for record in records
                if record["status"] in {"pending_preparation", "pending_training"}
            }
            if len(set(run_ids)) != len(run_ids) or set(run_ids) != set(pending):
                raise ValueError("The queue changed. Refresh and include every pending entry exactly once.")
            for order, run_id in enumerate(run_ids):
                record = pending[run_id]
                record["order"] = order
                connection.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(record), run_id))


class DeviceLease:
    def __init__(self, state: Path, path: Path | None = None):
        self.path = path or Path(tempfile.gettempdir()) / "musubi-gui-gpu-0.sqlite3"
        self.state = str(state.resolve())

    def owner(self) -> dict[str, Any] | None:
        with database(self.path) as connection:
            row = connection.execute("SELECT data FROM records WHERE id='gpu-0'").fetchone()
            return json.loads(row[0]) if row else None

    def acquire(self, run_id: str, identity: dict[str, Any]) -> bool:
        with database(self.path) as connection:
            if connection.execute("SELECT id FROM records WHERE id='gpu-0'").fetchone():
                return False
            connection.execute(
                "INSERT INTO records VALUES ('gpu-0',?)",
                (json.dumps({"state": self.state, "run_id": run_id, "controller": identity, "processes": []}),),
            )
            return True

    def update(self, run_id: str, processes: list[dict[str, Any]], **details: Any) -> None:
        with database(self.path) as connection:
            row = connection.execute("SELECT data FROM records WHERE id='gpu-0'").fetchone()
            if row:
                data = json.loads(row[0])
                if data["state"] == self.state and data["run_id"] == run_id:
                    data["processes"] = processes
                    data.update(details)
                    connection.execute("UPDATE records SET data=? WHERE id='gpu-0'", (json.dumps(data),))

    def release(self, run_id: str) -> None:
        with database(self.path) as connection:
            row = connection.execute("SELECT data FROM records WHERE id='gpu-0'").fetchone()
            if row:
                data = json.loads(row[0])
                if data["state"] == self.state and data["run_id"] == run_id:
                    connection.execute("DELETE FROM records WHERE id='gpu-0'")

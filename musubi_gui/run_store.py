"""Transactional run history and host-wide, durable GPU ownership."""

import builtins
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

# Runs that are queued, working or not yet reconciled, so their job stays in the list.
ACTIVE_STATUSES = frozenset(
    {
        "pending_preparation",
        "preparing",
        "pending_training",
        "training",
        "generating",
        "stopping",
        "orphaned",
        "uncertain",
    }
)


def is_active(run: dict[str, Any]) -> bool:
    return run["status"] in ACTIVE_STATUSES or bool(run.get("processes"))


def references_folder(run: dict[str, Any], folder: str) -> bool:
    """Whether `run` reads files under `folder`, through its frozen inputs, a stage argument or restored state.

    Retries and restored segments keep reading the media and dataset files of the Run they came from.
    """
    target = os.path.normcase(folder)

    def inside(text: str) -> bool:
        value = os.path.normcase(text)
        return value.endswith(target) or target + os.sep in value

    source = run.get("state_source") or {}
    return any(
        inside(text)
        for text in [
            *run.get("input_files", {}),
            *[arg for stage in run.get("stages", []) for arg in stage.get("args", [])],
            *([source["path"]] if "path" in source else []),
        ]
    )


@contextmanager
def preparation_transaction(root: Path) -> Iterator[None]:
    """A separate SQLite gate reserves identification/materialization across backends."""
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "snapshot-lock.sqlite3", timeout=30, isolation_level=None)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise ValueError(
                "Another request is creating a frozen snapshot. Wait for preparation to finish and retry; source files are preserved."
            ) from error
        try:
            yield
        finally:
            connection.rollback()


def blocks_revision(run: dict[str, Any], fingerprint: str) -> bool:
    """Whether `run` stands for this revision, so preparing it again returns `run` instead of a new Run.

    A run that ended without completing doesn't, unless it still records processes to reconcile,
    and nor does one being deleted.
    """
    ended_early = run["status"] in {"failed", "cancelled", "stopped", "interrupted", "deleting"} and not run.get(
        "processes"
    )
    return run["fingerprint"] == fingerprint and not ended_early


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

    def has_active_run(self, job_id: str) -> bool:
        return any(run["job"]["id"] == job_id and is_active(run) for run in self.list())

    def count_for_job(self, job_id: str) -> int:
        return sum(run["job"]["id"] == job_id for run in self.list())

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
                if blocks_revision(previous, data["fingerprint"]):
                    return dict(previous)
                if previous["status"] == "deleting" and references_folder(data, previous["directory"]):
                    raise ValueError("The Run these inputs come from is being deleted.")
            connection.execute("INSERT INTO records VALUES (?,?)", (data["id"], json.dumps(data)))
            return data

    def remove(self, run_id: str) -> None:
        with database(self.path) as connection:
            connection.execute("DELETE FROM records WHERE id=?", (run_id,))

    def update_with_others(
        self, run_id: str, change: Callable[[dict[str, Any], builtins.list[dict[str, Any]]], None]
    ) -> dict[str, Any]:
        """Like `update`, with every other Run read in the same transaction so `change` can depend on them."""
        with database(self.path) as connection:
            records = [json.loads(row[0]) for row in connection.execute("SELECT data FROM records")]
            data = next((record for record in records if record["id"] == run_id), None)
            if data is None:
                raise ValueError("Run not found.")
            change(data, [record for record in records if record["id"] != run_id])
            connection.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(data), run_id))
            return dict(data)

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

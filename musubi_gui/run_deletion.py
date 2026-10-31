"""What permanently deleting a Run may remove, what refuses it, and removing it without following links."""

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any

from musubi_gui.run_store import is_active, references_folder
from musubi_gui.settings import Settings

REMOVE_ATTEMPTS = 4
REMOVE_RETRY_SECONDS = 0.5


def output_index_file(root: Path, run_id: str) -> Path:
    return root / "output-index" / (hashlib.sha256(run_id.encode()).hexdigest() + ".json")


def _run_folder(recorded: str, configured: str, label: str) -> Path:
    """The recorded folder, proven to be `<root>/runs/<token>` once links are resolved."""
    if not configured or not Path(configured).is_absolute():
        raise ValueError(f"The {label} root is not configured. Restore it in Settings to delete this Run.")
    folder = Path(recorded)
    if folder.is_symlink() or folder.is_junction():
        raise ValueError(f"This Run's {label} folder is a link, so it is not deleted: {recorded}")
    if folder.resolve().parent != Path(configured).resolve() / "runs":
        raise ValueError(
            f"This Run's {label} folder is not a Run folder under the current {label} root: {recorded}. "
            "Restore its original root in Settings to delete it. Nothing was removed."
        )
    return folder


def plan_deletion(run: dict[str, Any], others: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    """The folders to remove and the Runs affected, or a ValueError giving why this Run can't be deleted."""
    if is_active(run):
        if run.get("processes"):
            raise ValueError(
                "This Run still records processes. Choose Check for leftover processes before deleting it."
            )
        raise ValueError(f"This Run is active ({run['status']}). Stop or cancel it before deleting it.")
    directory = str(_run_folder(run["directory"], settings.output_root, "output"))
    cache = str(_run_folder(run["cache"], settings.cache_root, "cache"))
    dependents = [other for other in others if references_folder(other, directory)]
    waiting = [other for other in dependents if is_active(other) or other["status"] == "prepared"]
    if waiting:
        names = ", ".join(f"{other['id'][:8]} ({other['status']})" for other in waiting)
        raise ValueError(f"Run {names} still needs this Run's frozen inputs or saved state. Cancel or delete it first.")
    # A restored segment works from its source's cache folder, so the last Run using it removes it.
    shared = any(Path(other.get("cache", "")) == Path(cache) or references_folder(other, cache) for other in others)
    return {
        "folders": [directory] if shared else [directory, cache],
        "shared_cache": cache if shared else None,
        "dependents": [
            {"id": other["id"], "status": other["status"]} for other in dependents if other["status"] != "deleting"
        ],
    }


def folder_bytes(folder: Path) -> int:
    """Bytes in the folder itself, not what a link inside it points to. os.walk enters junctions, so it can't be used."""
    total = 0
    pending = [folder]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and not entry.is_junction():
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def remove_folder(folder: Path) -> list[str]:
    """Remove all that can be removed, unlinking links rather than entering them; returns what could not be."""
    failed: list[str] = []

    def note(_: object, path: str, error: BaseException) -> None:
        if not isinstance(error, FileNotFoundError):
            failed.append(path)

    # Short retries outlast brief holds, such as a closing preview stream or a virus scanner.
    for attempt in range(REMOVE_ATTEMPTS):
        failed.clear()
        if os.path.lexists(folder):
            shutil.rmtree(folder, onexc=note)
        if not failed:
            break
        if attempt + 1 < REMOVE_ATTEMPTS:
            time.sleep(REMOVE_RETRY_SECONDS)
    # A folder only fails because something inside it did, so name the files where there are any.
    return [path for path in failed if not os.path.isdir(path)] or failed


def failure_message(failed: list[str]) -> str:
    return (
        f"The delete did not finish: {len(failed)} item(s) could not be removed, including {failed[0]}. "
        "Another program may have it open. Close it, then choose Delete again."
    )

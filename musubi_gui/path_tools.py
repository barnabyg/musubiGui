"""Local path checks and a names-only browser for the path fields; file contents are never read."""

import os
import string
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from musubi_gui.settings import within_root

Kind = Literal["folder", "file"]


class PathCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Kind
    roots: list[str] = []


class PathBrowse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = ""
    kind: Kind = "folder"


def unsupported(value: str) -> str | None:
    if "\x00" in value:
        return "This path contains an invalid character."
    if value.startswith(("\\\\", "//")) or "://" in value:
        return "Network paths and URLs are not supported; select a local path."
    if not Path(value).is_absolute():
        return "Use an absolute local path."
    return None


def check_path(value: str, kind: Kind, roots: list[str]) -> dict[str, str]:
    """Advice for a field being edited. Saving and running still enforce the same rules themselves."""
    value = value.strip()
    if not value:
        return {"state": "empty", "message": ""}
    problem = unsupported(value)
    if problem:
        return {"state": "invalid", "message": problem}
    path = Path(value)
    try:
        if roots:
            within_root(path, [Path(root) for root in roots])
    except ValueError:
        return {"state": "outside-root", "message": "This is outside the configured roots."}
    except OSError:
        return {"state": "invalid", "message": "This path can't be resolved."}
    try:
        if not path.exists():
            return {"state": "missing", "message": f"This {kind} doesn't exist."}
        if path.is_dir() != (kind == "folder"):
            return {"state": "wrong-type", "message": f"This is not a {kind}."}
    except OSError:
        return {"state": "invalid", "message": "This path can't be read."}
    return {"state": "ok", "message": ""}


def browse_path(value: str, kind: Kind) -> dict[str, Any]:
    """List one folder's names. An empty path lists the drives, the only places with no parent."""
    value = value.strip()
    if not value:
        return {"path": "", "parent": None, "entries": [_entry(Path(drive), True) for drive in _drives()]}
    problem = unsupported(value)
    if problem:
        raise ValueError(problem)
    folder = Path(value)
    # Opening on a file, or a folder that doesn't exist yet, shows the closest folder that does.
    try:
        while not folder.is_dir():
            if folder.parent == folder:
                return browse_path("", kind)
            folder = folder.parent
    except OSError as error:
        raise ValueError("This folder can't be read; check its permissions.") from error
    entries = []
    try:
        with os.scandir(folder) as found:
            for item in found:
                try:
                    is_folder = item.is_dir()
                except OSError:
                    continue
                if is_folder or kind == "file":
                    entries.append(_entry(Path(item.path), is_folder))
    except OSError as error:
        raise ValueError("This folder can't be listed; check its permissions.") from error
    entries.sort(key=lambda entry: (entry["kind"] != "folder", entry["name"].lower()))
    parent = None if folder.parent == folder else str(folder.parent)
    return {"path": str(folder), "parent": parent if parent is not None else "", "entries": entries}


def _drives() -> list[str]:
    if os.name == "nt":
        return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
    return ["/"]


def _entry(path: Path, is_folder: bool) -> dict[str, str]:
    return {"name": path.name or str(path), "path": str(path), "kind": "folder" if is_folder else "file"}


def paths_router() -> APIRouter:
    # POST only: the application's origin and content-type checks cover POST, so another site can't list folders.
    router = APIRouter(prefix="/api/paths")

    @router.post("/check")
    def check(request: PathCheck) -> dict[str, str]:
        return check_path(request.path, request.kind, request.roots)

    @router.post("/browse")
    def browse(request: PathBrowse) -> dict[str, Any]:
        try:
            return browse_path(request.path, request.kind)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router

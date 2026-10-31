"""Persistent output discovery, constrained file delivery and factual run comparison."""

import hashlib
import json
import mimetypes
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from musubi_gui.media_probe import inspect
from musubi_gui.redaction import redact
from musubi_gui.run_deletion import output_index_file
from musubi_gui.runs import RunService
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import atomic_json, load_settings, within_root

_lock = threading.RLock()
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEOS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
AUDIO = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
CHECKPOINTS = {".safetensors", ".pt", ".pth", ".ckpt"}
BEING_DELETED = "This Run is being deleted, so its outputs are no longer offered."
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"{port}{n}" for port in ["COM", "LPT"] for n in range(1, 10))}


def download_name(job: dict[str, Any], file: Path) -> str:
    """Name a downloaded LoRA after its job, since the name on disk carries only the job's ID."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(job.get("name", ""))).strip(" .")[:120].strip(" .") or "job"
    if name.split(".")[0].strip().upper() in WINDOWS_RESERVED:
        name = "_" + name
    # A saved state's weights sit in `<name>[-stepN]-state/`, and must not pass for the final LoRA.
    state = file.parent.name.endswith("-state")
    source = file.parent.name.removesuffix("-state") if state else file.stem
    step = re.search(r"-step(\d+)$", source)
    tag = f"-step{int(step[1])}" if step else ""
    return f"{name}-r{job.get('revision', 0)}{tag}{f'-state-{file.stem}' if state else ''}{file.suffix}"


def output_id(run_id: str, relative: str) -> str:
    return hashlib.sha256((run_id + "\0" + relative).encode()).hexdigest()[:32]


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _scrub(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_scrub(child) for child in value]
    return value


def checkpoint_metadata(file: Path) -> dict[str, Any]:
    """Read only bounded safetensors JSON; never unpickle a training artifact."""
    result: dict[str, Any] = {
        "training_state": False,
        "note": "A checkpoint alone does not establish complete/resumable optimizer state.",
    }
    if file.suffix.lower() != ".safetensors":
        return {
            **result,
            "checkpoint": {},
            "note": "Legacy artifact: contents are not loaded; resumability is unestablished.",
        }
    header = safetensors_header(file)
    with file.open("rb") as stream:
        header_bytes = int.from_bytes(stream.read(8), "little")
    available = file.stat().st_size - 8 - header_bytes
    tensors = [value for key, value in header.items() if key != "__metadata__"]
    for tensor in tensors:
        if not isinstance(tensor, dict):
            raise ValueError("Checkpoint tensor metadata is malformed.")
        offsets = tensor.get("data_offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(isinstance(value, int) for value in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= available
        ):
            raise ValueError("Checkpoint payload is incomplete or has invalid tensor offsets.")
    metadata = header.get("__metadata__", {})
    return {**result, "checkpoint": _scrub(metadata), "tensor_count": len(tensors)}


class OutputBrowser:
    def __init__(self, service: RunService):
        self.service = service
        self.root = service.root / "output-index"

    def _index(self, run_id: str) -> Path:
        return output_index_file(self.service.root, run_id)

    def _load(self, run_id: str) -> dict[str, dict[str, Any]]:
        file = self._index(run_id)
        if not file.exists():
            return {}
        data = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("run_id") != run_id:
            raise ValueError("Unsupported output-index format. Preserve it and use a compatible application.")
        entries = data.get("outputs")
        if not isinstance(entries, dict):
            raise ValueError("Output index is unreadable. Preserve it before repair.")
        for identity, entry in entries.items():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or identity != output_id(run_id, entry["path"])
            ):
                raise ValueError("Output index identity mismatch. Preserve it before repair.")
        return dict(entries)

    def _directory(self, run: dict[str, Any]) -> Path:
        original = str(run.get("settings", {}).get("output_root", ""))
        current = load_settings(self.service.root).output_root
        if not original or not current or not Path(original).is_absolute() or not Path(current).is_absolute():
            raise HTTPException(403, "Output access requires both the recorded and current absolute output roots.")
        try:
            directory = within_root(Path(run["directory"]), [Path(original)])
            return within_root(directory, [Path(current)])
        except ValueError as error:
            raise HTTPException(
                403,
                "This run is outside the current or recorded output root. Restore the original root in Settings to inspect it.",
            ) from error

    def _file(self, run: dict[str, Any], relative: str) -> Path:
        directory = self._directory(run)
        try:
            file = within_root(directory / relative, [directory])
            if (
                Path(relative).is_absolute()
                or Path(relative).parts[0] not in {"checkpoints", "samples"}
                or file.suffix.lower() not in IMAGES | VIDEOS | AUDIO | CHECKPOINTS
            ):
                raise ValueError("Unsupported output location.")
            return file
        except (ValueError, IndexError) as error:
            raise HTTPException(403, "Output path escapes its run or uses an unsupported location.") from error

    def scan(self, run_id: str) -> dict[str, Any]:
        with _lock:
            run = self.service.get(run_id)
            if run["status"] == "deleting":
                return self._result(run, {}, [BEING_DELETED])
            entries = self._load(run_id)
            warnings = []
            try:
                directory = self._directory(run)
            except HTTPException as error:
                for entry in entries.values():
                    entry.update(status="blocked", error=str(error.detail))
                return self._result(run, entries, [str(error.detail)])
            candidates: set[str] = set(entries)
            try:
                for subdirectory in ["checkpoints", "samples"]:
                    for file in (directory / subdirectory).rglob("*"):
                        if file.is_file() and file.suffix.lower() in IMAGES | VIDEOS | AUDIO | CHECKPOINTS:
                            relative = file.relative_to(directory).as_posix()
                            identity = output_id(run_id, relative)
                            candidates.add(identity)
                            entries.setdefault(
                                identity, {"id": identity, "run_id": run_id, "path": relative, "name": file.name}
                            )
            except OSError:
                warnings.append(
                    "Some output folders could not be read. Restore permissions and refresh; known entries are retained."
                )
            for identity in sorted(candidates):
                entry = entries[identity]
                try:
                    file = self._file(run, entry["path"])
                    if not file.is_file():
                        entry.update(
                            status="missing",
                            error="Output is missing or moved. Restore its original location and refresh.",
                        )
                        continue
                    stat = file.stat()
                    stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
                    suffix = file.suffix.lower()
                    kind = (
                        "checkpoint"
                        if suffix in CHECKPOINTS
                        else "image"
                        if suffix in IMAGES
                        else "video"
                        if suffix in VIDEOS
                        else "audio"
                    )
                    entry.update(kind=kind, bytes=stat.st_size, file_url=f"/api/runs/{run_id}/outputs/{identity}/file")
                    if not stat.st_size:
                        raise ValueError("Output is empty; its producer may still be writing it.")
                    if entry.get("stamp") != stamp or entry.get("status") != "available":
                        metadata = checkpoint_metadata(file) if kind == "checkpoint" else inspect(file)
                        entry.update(metadata=_scrub(metadata), stamp=stamp)
                        if kind == "video" and run["job"].get("family") == "h3" and not metadata.get("audio"):
                            raise ValueError(
                                "H3 T2VA sample has no decodable audio stream. Video-only output does not establish complete joint audio/video sampling."
                            )
                    entry.update(status="available", error=None)
                except HTTPException as error:
                    entry.update(status="blocked", error=str(error.detail))
                except PermissionError:
                    entry.update(status="unreadable", error="Output is unreadable. Restore permissions and refresh.")
                except FileNotFoundError:
                    entry.update(
                        status="missing",
                        error="Output disappeared during inspection. Refresh after its producer finishes.",
                    )
                except (ValueError, OSError, TypeError, KeyError) as error:
                    entry.update(status="incomplete", error=redact(str(error)))
                except Exception as error:
                    # PyAV/Pillow expose several decoder exception classes. No source file is changed.
                    entry.update(
                        status="incomplete",
                        error=f"Output cannot be decoded ({type(error).__name__}). It may still be writing; refresh or inspect its producer log.",
                    )
            self.root.mkdir(parents=True, exist_ok=True)
            atomic_json(self._index(run_id), {"version": 1, "run_id": run_id, "outputs": entries})
            return self._result(run, entries, warnings)

    def _result(self, run: dict[str, Any], entries: dict[str, dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
        return {
            "run_id": run["id"],
            "job": run["job"],
            "status": run["status"],
            "outputs": sorted(entries.values(), key=lambda entry: entry["path"]),
            "warnings": warnings,
            "note": "These files belong to this Run.",
        }

    def file(self, run_id: str, identity: str, download: bool) -> FileResponse:
        run = self.service.get(run_id)
        if run["status"] == "deleting":
            raise HTTPException(404, BEING_DELETED)
        self._directory(run)
        entries = self._load(run_id)
        if identity not in entries:
            self.scan(run_id)
            entries = self._load(run_id)
        if identity not in entries:
            raise HTTPException(404, "Output not found. Refresh the output browser.")
        entry = entries[identity]
        file = self._file(run, entry["path"])
        if not file.is_file():
            raise HTTPException(404, "Output is missing or moved; restore its original location.")
        kind = entry.get("kind")
        attachment = download or kind == "checkpoint" or entry.get("status") != "available"
        return FileResponse(
            file,
            media_type="application/octet-stream"
            if attachment
            else mimetypes.guess_type(file.name)[0] or "application/octet-stream",
            filename=(download_name(run["job"], file) if kind == "checkpoint" else file.name) if attachment else None,
        )


def _comparison_view(run: dict[str, Any]) -> dict[str, Any]:
    items = run.get("manifest", {}).get("items", [])
    return dict(
        _scrub(
            {
                "job": run.get("job", {}),
                "settings": run.get("settings", {}),
                "dataset": {
                    **run.get("manifest", {}),
                    "items": {str(item.get("id", item.get("path", index))): item for index, item in enumerate(items)},
                },
                "provenance": run.get("provenance", {}),
            }
        )
    )


def compare_runs(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    changes = []

    def compare(before: Any, after: Any, path: str) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                compare(before.get(key), after.get(key), f"{path}.{key}" if path else key)
        elif before != after:
            changes.append({"path": path, "before": before, "after": after})

    compare(_comparison_view(first), _comparison_view(second), "")
    return {
        "before": first["id"],
        "after": second["id"],
        "changes": changes,
        "quality_ranking": None,
        "note": "This compares what each Run recorded as its inputs, not the quality of its results.",
    }


@contextmanager
def output_errors() -> Iterator[None]:
    try:
        yield
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(
            500,
            "Output records could not be read or saved. Check state permissions and free disk; source outputs are preserved.",
        ) from error


def output_router(service: RunService) -> APIRouter:
    router = APIRouter(prefix="/api/runs")
    browser = OutputBrowser(service)

    @router.get("/{run_id}/outputs")
    def outputs(run_id: str) -> dict[str, Any]:
        with output_errors():
            return browser.scan(run_id)

    @router.get("/{run_id}/outputs/{identity}/file")
    def file(run_id: str, identity: str, download: bool = False) -> FileResponse:
        with output_errors():
            return browser.file(run_id, identity, download)

    @router.get("/{run_id}/compare/{other_id}")
    def compare(run_id: str, other_id: str) -> dict[str, Any]:
        with output_errors():
            return compare_runs(service.get(run_id), service.get(other_id))

    return router

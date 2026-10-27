"""Persistent dataset library. Original media stays authoritative and inside configured roots."""

import builtins
import hashlib
import json
import mimetypes
import os
import sqlite3
import stat
import subprocess  # nosec B404
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from musubi_gui.settings import atomic_json, load_settings, within_root

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEOS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


def content_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class CaptionConflict(ValueError):
    """The authoritative sidecar changed after the user loaded it."""


def read_caption(path: Path) -> dict[str, Any]:
    exists = path.exists()
    data = path.read_bytes() if exists else b""
    return {
        "text": data.decode("utf-8-sig"),
        "revision": hashlib.sha256((b"exists:" if exists else b"missing:") + data).hexdigest(),
        "exists": exists,
    }


def regular_path(path: Path, root: Path) -> Path:
    """Reject redirection even inside a root so one item's sidecar cannot target another file."""
    candidate = path.absolute()
    resolved = within_root(candidate, [root])
    error = "Linked or redirected dataset paths cannot be edited or served. Use regular local media/sidecars, or register the resolved physical folder. Linked targets are preserved."
    if candidate != resolved:
        raise ValueError(error)
    current = candidate
    while current.is_relative_to(root):
        try:
            info = current.lstat()
        except FileNotFoundError:
            info = None
        if info and (
            stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(error)
        if current == root:
            break
        current = current.parent
    return candidate


class DatasetStore:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "datasets.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise ValueError(
                    "Unsupported dataset library version. Preserve datasets.sqlite3 and restore a compatible application."
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, name TEXT, path TEXT UNIQUE COLLATE NOCASE, managed INTEGER, scan TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, dataset TEXT, relative TEXT, stamp TEXT, metadata TEXT, valid INTEGER, error TEXT, excluded INTEGER DEFAULT 0, explicit_audio TEXT DEFAULT '', UNIQUE(dataset,relative))"
            )
            db.execute("PRAGMA user_version=1")
            db.commit()
            with db:
                yield db
        finally:
            db.close()

    def _folder(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute() or value.startswith(("\\\\", "//")) or "://" in value or "\x00" in value:
            raise ValueError("Select an absolute local folder inside a configured dataset root.")
        settings = load_settings(self.root)
        return within_root(path, [Path(value) for value in settings.dataset_roots])

    def register(self, name: str, path: str, managed: bool = False) -> dict[str, Any]:
        if not name.strip() or len(name) > 200:
            raise ValueError("Give the dataset a name of 1–200 characters.")
        folder = self._folder(path)
        if not folder.is_dir():
            raise ValueError("Dataset folder does not exist or is unreadable.")
        with self._db() as db:
            old = db.execute("SELECT id FROM datasets WHERE path=?", (str(folder),)).fetchone()
            if old:
                return self.get(str(old["id"]))
            dataset_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO datasets VALUES (?,?,?,?,?)",
                (
                    dataset_id,
                    name.strip(),
                    str(folder),
                    int(managed),
                    json.dumps({"state": "pending", "completed": 0, "total": 0}),
                ),
            )
        return self.get(dataset_id)

    def get(self, dataset_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if row is None:
                raise KeyError("Dataset was not found.")
            rows = db.execute("SELECT valid,excluded,relative FROM items WHERE dataset=?", (dataset_id,)).fetchall()
        counts = {"total": len(rows), "valid": 0, "included": 0, "captioned": 0, "excluded": 0}
        missing = not Path(row["path"]).is_dir()
        for item in rows:
            counts["valid"] += int(item["valid"])
            counts["excluded"] += int(item["excluded"])
            counts["included"] += int(bool(item["valid"]) and not item["excluded"])
            try:
                caption = self._folder(row["path"]) / item["relative"]
                caption = within_root(caption.with_suffix(".txt"), [self._folder(row["path"])])
                counts["captioned"] += int(caption.is_file() and bool(caption.read_text(encoding="utf-8").strip()))
            except (OSError, ValueError):
                pass
        return {
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "managed": bool(row["managed"]),
            "missing": missing,
            "counts": counts,
            "scan": json.loads(row["scan"]),
        }

    def list(self) -> list[dict[str, Any]]:
        with self._db() as db:
            identifiers = [str(row[0]) for row in db.execute("SELECT id FROM datasets ORDER BY name,id")]
        return [self.get(identifier) for identifier in identifiers]

    def rename(self, dataset_id: str, name: str) -> dict[str, Any]:
        if not name.strip() or len(name) > 200:
            raise ValueError("Give the dataset a name of 1–200 characters.")
        with self._db() as db:
            if not db.execute("UPDATE datasets SET name=? WHERE id=?", (name.strip(), dataset_id)).rowcount:
                raise KeyError("Dataset was not found.")
        return self.get(dataset_id)

    def remove(self, dataset_id: str) -> dict[str, str]:
        """Remove the library index only; media, sidecars and recovery evidence stay intact."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT scan FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if row is None:
                raise KeyError("Dataset was not found.")
            if json.loads(row["scan"])["state"] in {"pending", "scanning"}:
                raise ValueError("Wait for the dataset scan to finish before deleting its library entry.")
            db.execute("DELETE FROM items WHERE dataset=?", (dataset_id,))
            db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
        return {"id": dataset_id}

    def _progress(self, dataset_id: str, status: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("UPDATE datasets SET scan=? WHERE id=?", (json.dumps(status), dataset_id))

    def probe(
        self, path: Path, thumbnail: Path | None = None, timeline: bool = False, extension: str | None = None
    ) -> dict[str, Any]:
        args = [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).with_name("media_probe.py")), str(path)]
        if thumbnail:
            thumbnail.parent.mkdir(parents=True, exist_ok=True)
            args.extend(["--thumbnail", str(thumbnail)])
        if timeline:
            args.append("--timeline")
        if extension:
            args.extend(["--extension", extension])
        # B603: app interpreter, fixed read-only media probe, and contained file paths.
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45
            )  # nosec B603
        except subprocess.TimeoutExpired:
            return {
                "valid": False,
                "error": "Media inspection exceeded 45 seconds. Use a shorter clip or exclude this item; the original source is preserved.",
            }
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("MUSUBI_MEDIA=") and result.returncode == 0:
                return dict(json.loads(line.removeprefix("MUSUBI_MEDIA=")))
        return {
            "valid": False,
            "error": "The application media decoder failed. Repair the locked application environment and re-scan this item.",
        }

    def scan(self, dataset_id: str) -> dict[str, Any]:
        folder = self.folder(dataset_id)
        if not folder.is_dir():
            self._progress(
                dataset_id,
                {
                    "state": "failed",
                    "completed": 0,
                    "total": 0,
                    "error": "Dataset folder is missing. Restore its original location and re-scan.",
                },
            )
            return self.get(dataset_id)
        paths = sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in IMAGES | VIDEOS)
        seen: set[str] = set()
        self._progress(dataset_id, {"state": "scanning", "completed": 0, "total": len(paths)})
        for index, path in enumerate(paths):
            relative = path.relative_to(folder).as_posix()
            seen.add(relative)
            item_id = hashlib.sha256((dataset_id + "/" + relative).encode()).hexdigest()[:32]
            try:
                resolved = regular_path(path, folder)
                info = resolved.stat()
                stamp = f"{info.st_mtime_ns}:{info.st_size}"
                with self._db() as db:
                    old = db.execute("SELECT stamp,valid FROM items WHERE id=?", (item_id,)).fetchone()
                if (
                    old
                    and old["valid"]
                    and old["stamp"] == stamp
                    and (self.root / "thumbnails" / f"{item_id}.jpg").is_file()
                ):
                    continue
                inspected = self.probe(resolved, self.root / "thumbnails" / f"{item_id}.jpg")
            except ValueError as error:
                stamp = ""
                inspected = {"valid": False, "error": str(error)}
            except (OSError, subprocess.SubprocessError):
                stamp = ""
                inspected = {
                    "valid": False,
                    "error": "Media is unreadable or escapes the registered folder. Restore access or exclude this item.",
                }
            with self._db() as db:
                db.execute(
                    "INSERT INTO items(id,dataset,relative,stamp,metadata,valid,error) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET stamp=excluded.stamp,metadata=excluded.metadata,valid=excluded.valid,error=excluded.error",
                    (
                        item_id,
                        dataset_id,
                        relative,
                        stamp,
                        json.dumps(inspected.get("metadata", {})),
                        int(inspected["valid"]),
                        inspected.get("error", ""),
                    ),
                )
            self._progress(dataset_id, {"state": "scanning", "completed": index + 1, "total": len(paths)})
        with self._db() as db:
            for row in db.execute("SELECT id,relative FROM items WHERE dataset=?", (dataset_id,)).fetchall():
                if row["relative"] not in seen:
                    db.execute(
                        "UPDATE items SET valid=0,error=? WHERE id=?",
                        ("Source media is missing. Restore it and re-scan.", row["id"]),
                    )
        self._progress(dataset_id, {"state": "complete", "completed": len(paths), "total": len(paths)})
        return self.get(dataset_id)

    def media_path(self, dataset_id: str, item_id: str) -> Path:
        folder = self.folder(dataset_id)
        with self._db() as db:
            row = db.execute("SELECT relative FROM items WHERE dataset=? AND id=?", (dataset_id, item_id)).fetchone()
        if row is None:
            raise KeyError("Dataset item was not found.")
        path = regular_path(folder / row["relative"], folder)
        if not path.is_file():
            raise KeyError("Source media is missing. Restore it and re-scan.")
        return path

    def folder(self, dataset_id: str) -> Path:
        stored = Path(self.get(dataset_id)["path"])
        self._folder(str(stored))
        return regular_path(stored, stored)

    def caption(self, dataset_id: str, item_id: str) -> dict[str, Any]:
        media = self.media_path(dataset_id, item_id)
        path = regular_path(media.with_suffix(".txt"), self.folder(dataset_id))
        return read_caption(path)

    def save_caption(
        self, dataset_id: str, item_id: str, text: str, revision: str, exists: bool = True
    ) -> dict[str, Any]:
        if len(text.encode("utf-8")) > 2 * 1024**2:
            raise ValueError("Caption exceeds the 2 MiB text limit.")
        media = self.media_path(dataset_id, item_id)
        folder = self.folder(dataset_id)
        path = regular_path(media.with_suffix(".txt"), folder)
        history_root = self.root / "caption-history" / item_id
        history_root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            original = read_caption(path)
            if original["revision"] != revision:
                raise CaptionConflict(
                    "Caption changed outside this editor. Your text is retained. Reload the current sidecar before deciding which text to save."
                )
            history_id = uuid.uuid4().hex
            record = {
                "version": 1,
                "id": history_id,
                "dataset_id": dataset_id,
                "item_id": item_id,
                "created_utc": datetime.now(UTC).isoformat(),
                "before": original,
                "after": {"text": text, "exists": exists},
                "state": "prepared",
            }
            history_file = history_root / f"{history_id}.json"
            atomic_json(history_file, record)
            descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-caption-", suffix=".txt")
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                regular_path(media, folder)
                regular_path(media.with_suffix(".txt"), folder)
                if read_caption(path)["revision"] != revision:
                    raise CaptionConflict(
                        "Caption changed during save. Your text is retained; reload and review the external edit."
                    )
                if exists:
                    os.replace(temporary, path)
                else:
                    path.unlink(missing_ok=True)
                saved = read_caption(path)
                record.update(state="applied", after=saved)
                atomic_json(history_file, record)
                return {**saved, "history_id": history_id}
            finally:
                Path(temporary).unlink(missing_ok=True)

    def caption_history(self, dataset_id: str, item_id: str) -> builtins.list[dict[str, Any]]:
        self.media_path(dataset_id, item_id)
        records = []
        for path in (self.root / "caption-history" / item_id).glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("version") != 1:
                raise ValueError("Unsupported caption history version. Preserve the history files.")
            records.append(record)
        return sorted(records, key=lambda value: value["created_utc"], reverse=True)

    def restore_caption(self, dataset_id: str, item_id: str, history_id: str, revision: str) -> dict[str, Any]:
        history = next(
            (value for value in self.caption_history(dataset_id, item_id) if value["id"] == history_id), None
        )
        if history is None:
            raise KeyError("Caption revision was not found.")
        before = history["before"]
        return self.save_caption(dataset_id, item_id, before["text"], revision, before["exists"])

    def items(self, dataset_id: str) -> builtins.list[dict[str, Any]]:
        dataset = self.get(dataset_id)
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM items WHERE dataset=? ORDER BY relative COLLATE NOCASE", (dataset_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = {
                "id": row["id"],
                "path": row["relative"],
                "name": row["relative"],
                "absolute_path": str(Path(dataset["path"]) / row["relative"]),
                "kind": "image" if Path(row["relative"]).suffix.lower() in IMAGES else "video",
                "valid": bool(row["valid"]),
                "error": row["error"],
                "excluded": bool(row["excluded"]),
                "metadata": json.loads(row["metadata"]),
            }
            try:
                item["caption"] = self.caption(dataset_id, row["id"])
            except (OSError, ValueError, KeyError) as error:
                item["caption"] = {"text": "", "revision": "", "exists": False, "error": str(error)}
            result.append(item)
        return result

    def mime_type(self, dataset_id: str, item_id: str) -> str:
        path = self.media_path(dataset_id, item_id)
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def manifest(self, dataset_id: str) -> dict[str, Any]:
        items = self.items(dataset_id)
        included = []
        for item in items:
            if item["valid"] and not item["excluded"]:
                path = self.media_path(dataset_id, item["id"])
                audio = self.inspect_h3(dataset_id, item["id"]) if item["kind"] == "video" else None
                included.append({**item, "absolute_path": str(path), "sha256": content_hash(path), "audio": audio})
        manifest = {
            "dataset": self.get(dataset_id),
            "items": included,
            "excluded": [item["id"] for item in items if item["excluded"]],
            "invalid": [
                {"id": item["id"], "name": item["name"], "error": item["error"]} for item in items if not item["valid"]
            ],
        }
        return {**manifest, "digest": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}

    def set_audio(self, dataset_id: str, item_id: str, path: str) -> dict[str, Any]:
        self.media_path(dataset_id, item_id)
        if path:
            if not Path(path).is_absolute():
                raise ValueError("Use an absolute audio path inside this registered dataset folder.")
            within_root(Path(path), [self._folder(self.get(dataset_id)["path"])])
        with self._db() as db:
            db.execute("UPDATE items SET explicit_audio=? WHERE id=? AND dataset=?", (path, item_id, dataset_id))
        return self.inspect_h3(dataset_id, item_id)

    def exclude(self, dataset_id: str, item_ids: builtins.list[str], excluded: bool) -> dict[str, Any]:
        known = {item["id"] for item in self.items(dataset_id)}
        if not item_ids or not set(item_ids).issubset(known):
            raise ValueError("Select existing items in this dataset before changing exclusions.")
        with self._db() as db:
            for item_id in set(item_ids):
                db.execute("UPDATE items SET excluded=? WHERE dataset=? AND id=?", (int(excluded), dataset_id, item_id))
        return self.get(dataset_id)

    def create_managed(self, name: str, root: str) -> dict[str, Any]:
        folder = self._folder(root)
        folder.mkdir(parents=True, exist_ok=True)
        destination = within_root(folder / ("managed-" + uuid.uuid4().hex), [folder])
        destination.mkdir()
        try:
            return self.register(name, str(destination), managed=True)
        except Exception:
            destination.rmdir()
            raise

    def inspect_h3(self, dataset_id: str, item_id: str, frames: int = 124) -> dict[str, Any]:
        path = self.media_path(dataset_id, item_id)
        folder = self._folder(self.get(dataset_id)["path"])
        errors: builtins.list[str] = []
        warnings: builtins.list[str] = []
        checked = self.probe(path, timeline=True)
        metadata = checked.get("metadata", {})
        inspection: dict[str, Any] = {
            "source": "missing",
            "audio_path": None,
            "errors": errors,
            "warnings": warnings,
            "coverage": 0.0,
            "metadata": metadata,
            "target_frames": frames,
            "target_fps": 24,
        }
        if not checked["valid"]:
            errors.append(checked.get("error", "Media cannot be decoded."))
            return inspection
        if path.suffix.lower() not in VIDEOS:
            errors.append("The initial H3 T2VA recipe requires video targets.")
        width, height = int(metadata.get("width", 0)), int(metadata.get("height", 0))
        if width <= 0 or height <= 0 or width % 32 or height % 32:
            errors.append(
                "H3 geometry requires positive width and height in multiples of 32. Re-encode or choose compatible training geometry."
            )
        if frames < 124 or frames > 345 or (frames - 5) % 17:
            errors.append("H3 target frames must follow 17*n+5 from 124 through 345 at 24 fps.")
        required = frames / 24
        if float(metadata.get("duration", 0)) + 1 / 48 < required:
            errors.append(f"Video is too short for {frames} frames at 24 fps; supply at least {required:.3f} seconds.")
        if float(metadata.get("max_video_backwards_seconds", 0)) > 4 / max(float(metadata.get("fps", 24)), 1):
            errors.append("Video timestamps must be nondecreasing. Re-encode the source timeline.")
        with self._db() as db:
            row = db.execute(
                "SELECT explicit_audio FROM items WHERE id=? AND dataset=?", (item_id, dataset_id)
            ).fetchone()
        explicit = str(row["explicit_audio"])
        audio_path: Path | None = None
        if explicit:
            inspection["source"] = "explicit"
            audio_path = within_root(Path(explicit), [folder])
        else:
            candidates = sorted(
                candidate
                for candidate in path.parent.iterdir()
                if candidate.is_file() and candidate.stem == path.stem and candidate.suffix.lower() in AUDIO
            )
            if len(candidates) > 1:
                errors.append(
                    "Multiple same-stem audio sidecars are ambiguous: "
                    + ", ".join(candidate.name for candidate in candidates)
                    + ". Choose an explicit audio source."
                )
                inspection["source"] = "ambiguous"
                return inspection
            if candidates:
                inspection["source"] = "sidecar"
                audio_path = within_root(candidates[0], [folder])
            elif metadata.get("audio"):
                inspection["source"] = "embedded"
                audio_path = path
        if audio_path is None:
            warnings.append(
                "No audio is supplied. Upstream uses a silence placeholder excluded from audio supervision; choose allow-missing audio policy to prepare this item."
            )
            return inspection
        inspection["audio_path"] = str(audio_path)
        try:
            audio_checked = checked if audio_path == path else self.probe(audio_path, timeline=True)
        except (OSError, subprocess.SubprocessError):
            audio_checked = {
                "valid": False,
                "error": "Supplied audio cannot be decoded. Restore or re-encode that source.",
            }
        audio = audio_checked.get("metadata", {}).get("audio")
        if not audio_checked["valid"] or not audio:
            errors.append(audio_checked.get("error", "Supplied audio contains no decodable audio stream."))
            return inspection
        inspection["audio_metadata"] = audio
        inspection["audio_sha256"] = content_hash(audio_path)
        offset = (
            float(audio["start"]) - float(metadata.get("video_start", 0)) if inspection["source"] == "embedded" else 0.0
        )
        inspection["offset_seconds"] = offset
        if abs(offset) > 0.025:
            warnings.append(
                f"Embedded audio is offset by {offset:.3f} seconds from the first video frame; inspect alignment before preparation."
            )
        jumps = audio.get("timestamp_jumps", [])
        wobble = (
            jumps
            and any(jump["seconds"] > 0 for jump in jumps)
            and any(jump["seconds"] < 0 for jump in jumps)
            and max(abs(jump["seconds"]) for jump in jumps) <= 0.05
        )
        missing = max(0.0, offset) + max(0.0, required - (offset + float(audio["duration"])))
        if not wobble:
            for jump in jumps:
                if abs(jump["seconds"]) > 0.05:
                    errors.append(
                        "Audio stream is discontinuous beyond the pinned 50 ms tolerance. Re-encode its timestamps."
                    )
                    break
                if 0 <= float(jump["at"]) + offset <= required:
                    missing += abs(float(jump["seconds"]))
        if missing <= 800 / 32000:
            missing = 0.0
        inspection["missing_seconds"] = missing
        inspection["coverage"] = round(max(0.0, min(1.0, (required - missing) / required)), 6)
        if missing > 0.2:
            errors.append(
                f"Audio is materially short or misaligned: {missing:.3f} seconds missing in this training window exceeds the pinned 200 ms limit. Re-encode aligned audio or exclude the item."
            )
        return inspection

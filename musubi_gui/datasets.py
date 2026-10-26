"""Persistent dataset library. Original media stays authoritative and inside configured roots."""

import builtins
import hashlib
import json
import mimetypes
import sqlite3
import subprocess  # nosec B404
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from musubi_gui.settings import load_settings, within_root

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEOS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


def content_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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

    def _progress(self, dataset_id: str, status: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("UPDATE datasets SET scan=? WHERE id=?", (json.dumps(status), dataset_id))

    def probe(self, path: Path, thumbnail: Path | None = None) -> dict[str, Any]:
        args = [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).with_name("media_probe.py")), str(path)]
        if thumbnail:
            thumbnail.parent.mkdir(parents=True, exist_ok=True)
            args.extend(["--thumbnail", str(thumbnail)])
        # B603: selected local training interpreter, fixed read-only media probe, and contained file paths.
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)  # nosec B603
        for line in reversed(result.stdout.splitlines()):
            if line.startswith("MUSUBI_MEDIA=") and result.returncode == 0:
                return dict(json.loads(line.removeprefix("MUSUBI_MEDIA=")))
        return {
            "valid": False,
            "error": "The selected training Python could not inspect this media. Check PyAV/Pillow in that environment.",
        }

    def scan(self, dataset_id: str) -> dict[str, Any]:
        dataset = self.get(dataset_id)
        folder = self._folder(dataset["path"])
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
                resolved = within_root(path, [folder])
                info = resolved.stat()
                stamp = f"{info.st_mtime_ns}:{info.st_size}"
                with self._db() as db:
                    old = db.execute("SELECT stamp FROM items WHERE id=?", (item_id,)).fetchone()
                if old and old["stamp"] == stamp:
                    continue
                inspected = self.probe(resolved, self.root / "thumbnails" / f"{item_id}.jpg")
            except (ValueError, OSError, subprocess.SubprocessError):
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
        folder = self._folder(self.get(dataset_id)["path"])
        with self._db() as db:
            row = db.execute("SELECT relative FROM items WHERE dataset=? AND id=?", (dataset_id, item_id)).fetchone()
        if row is None:
            raise KeyError("Dataset item was not found.")
        path = within_root(folder / row["relative"], [folder])
        if not path.is_file():
            raise KeyError("Source media is missing. Restore it and re-scan.")
        return path

    def caption(self, dataset_id: str, item_id: str) -> dict[str, Any]:
        media = self.media_path(dataset_id, item_id)
        path = within_root(media.with_suffix(".txt"), [self._folder(self.get(dataset_id)["path"])])
        exists = path.exists()
        data = path.read_bytes() if exists else b""
        return {
            "text": data.decode("utf-8-sig"),
            "revision": hashlib.sha256((b"exists:" if exists else b"missing:") + data).hexdigest(),
            "exists": exists,
        }

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
                included.append({**item, "absolute_path": str(path), "sha256": content_hash(path)})
        manifest = {
            "dataset": self.get(dataset_id),
            "items": included,
            "excluded": [item["id"] for item in items if item["excluded"]],
            "invalid": [
                {"id": item["id"], "name": item["name"], "error": item["error"]} for item in items if not item["valid"]
            ],
        }
        return {**manifest, "digest": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}

"""Bounded, restartable uploads; private staging and exclusive publication preserve completed files."""

import base64
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from musubi_gui.datasets import AUDIO, IMAGES, VIDEOS, CaptionConflict, DatasetStore
from musubi_gui.settings import atomic_json, within_root

CHUNK_BYTES = 512 * 1024


class DatasetUploads:
    def __init__(self, store: DatasetStore):
        self.store = store
        self.root = store.root / "uploads"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / "upload-lock.sqlite3", timeout=30)) as lock, lock:
            lock.execute("CREATE TABLE IF NOT EXISTS mutex (id INTEGER PRIMARY KEY)")
            lock.execute("BEGIN IMMEDIATE")
            yield

    def _folder(self, dataset_id: str) -> Path:
        dataset = self.store.get(dataset_id)
        if not dataset["managed"]:
            raise ValueError(
                "Uploads require a managed dataset; registered source folders are not upload destinations."
            )
        return self.store.folder(dataset_id)

    def _record(self, dataset_id: str, upload_id: str) -> dict[str, Any]:
        self._folder(dataset_id)
        if len(upload_id) != 32 or any(character not in "0123456789abcdef" for character in upload_id):
            raise ValueError("Invalid upload identity.")
        path = self.root / f"{upload_id}.json"
        if not path.is_file():
            raise KeyError("Upload was not found. Select the file and start a new upload.")
        record = dict(json.loads(path.read_text(encoding="utf-8")))
        if record.get("version") != 1 or record.get("dataset_id") != dataset_id:
            raise ValueError("Upload belongs to another dataset or an unsupported version; its files were preserved.")
        return record

    def _partial(self, dataset_id: str, upload_id: str) -> Path:
        folder = self._folder(dataset_id)
        staging = within_root(folder / ".musubi-pending-uploads", [folder])
        staging.mkdir(exist_ok=True)
        partial = staging / f"{upload_id}.part"
        if partial.is_symlink():
            raise ValueError("Upload staging files must not be symlinks. Preserve the source and cancel this upload.")
        return within_root(partial, [folder])

    def status(self, dataset_id: str, upload_id: str) -> dict[str, Any]:
        record = self._record(dataset_id, upload_id)
        partial = self._partial(dataset_id, upload_id)
        received = (
            partial.stat().st_size if partial.exists() else record["size"] if record["status"] == "complete" else 0
        )
        return {**record, "received": received}

    def list(self, dataset_id: str) -> list[dict[str, Any]]:
        self._folder(dataset_id)
        records = []
        for path in self.root.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("dataset_id") == dataset_id:
                records.append(self.status(dataset_id, value["id"]))
        return records

    def begin(self, dataset_id: str, filename: str, size: int) -> dict[str, Any]:
        folder = self._folder(dataset_id)
        suffix = Path(filename).suffix.lower()
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "CONIN$",
            "CONOUT$",
            *(f"COM{index}" for index in "¹²³"),
            *(f"LPT{index}" for index in "¹²³"),
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }
        if (
            not filename
            or len(filename) > 180
            or filename.startswith(".")
            or filename.endswith((".", " "))
            or any(character in filename for character in '\\/:\x00<>"|?*')
            or any(ord(character) < 32 for character in filename)
            or filename.split(".")[0].rstrip(" .").upper() in reserved
        ):
            raise ValueError(
                "Use a plain local filename without traversal, reserved Windows names, or special path characters."
            )
        if suffix not in IMAGES | VIDEOS | AUDIO | {".txt"}:
            raise ValueError("Unsupported upload type. Select image/video/audio media or matching UTF-8 .txt captions.")
        if (
            size < 0
            or size > 64 * 1024**3
            or (size == 0 and suffix != ".txt")
            or (suffix == ".txt" and size > 2 * 1024**2)
        ):
            raise ValueError("File size is invalid. Captions support up to 2 MiB; media supports up to 64 GiB.")
        if size + 64 * 1024**2 > shutil.disk_usage(folder).free:
            raise ValueError(
                "Insufficient free disk for this upload plus 64 MiB of working space. Free space or choose another dataset root."
            )
        with self._lock():
            if any(path.name.casefold() == filename.casefold() for path in folder.iterdir()):
                raise CaptionConflict(
                    "A file with this name already exists. Rename the upload; completed files are never overwritten."
                )
            if any(
                record["filename"].casefold() == filename.casefold() and record["status"] == "uploading"
                for record in self.list(dataset_id)
            ):
                raise CaptionConflict(
                    "An incomplete upload already exists for this name. Resume it by selecting the same file, or cancel its partial upload."
                )
            upload_id = uuid.uuid4().hex
            self._partial(dataset_id, upload_id).touch(exist_ok=False)
            record = {
                "version": 1,
                "id": upload_id,
                "dataset_id": dataset_id,
                "filename": filename,
                "size": size,
                "status": "uploading",
            }
            atomic_json(self.root / f"{upload_id}.json", record)
            return {**record, "received": 0}

    def chunk(self, dataset_id: str, upload_id: str, offset: int, data: str) -> dict[str, Any]:
        decoded = base64.b64decode(data, validate=True)
        if len(decoded) > CHUNK_BYTES or not decoded or offset < 0:
            raise ValueError("Upload chunks must contain 1–512 KiB and use a nonnegative byte offset.")
        with self._lock():
            record = self._record(dataset_id, upload_id)
            if record["status"] != "uploading":
                raise CaptionConflict("This upload is no longer active. Its completed source file is preserved.")
            partial = self._partial(dataset_id, upload_id)
            received = partial.stat().st_size
            if offset > received or offset + len(decoded) > record["size"]:
                raise CaptionConflict(
                    "Upload offset or size does not match the persisted partial file. Retry from its reported byte count."
                )
            with partial.open("r+b") as stream:
                stream.seek(offset)
                overlap = min(len(decoded), received - offset)
                if stream.read(overlap) != decoded[:overlap]:
                    raise CaptionConflict(
                        "The selected file differs from this incomplete upload. Cancel the partial upload before uploading a different file."
                    )
                stream.write(decoded[overlap:])
                stream.flush()
                os.fsync(stream.fileno())
            return self.status(dataset_id, upload_id)

    def complete(self, dataset_id: str, upload_id: str) -> dict[str, Any]:
        with self._lock():
            record = self._record(dataset_id, upload_id)
            if record["status"] == "complete":
                return self.status(dataset_id, upload_id)
            if record["status"] != "uploading":
                raise CaptionConflict("Upload was cancelled. Select the file to start again.")
            partial = self._partial(dataset_id, upload_id)
            if partial.stat().st_size != record["size"]:
                raise CaptionConflict(
                    "Upload is incomplete. Select the same file to resume; no partial file is shown as complete media."
                )
            folder = self._folder(dataset_id)
            filename = record["filename"]
            suffix = Path(filename).suffix.lower()
            if suffix == ".txt":
                partial.read_text(encoding="utf-8-sig")
                if not any(
                    path.stem == Path(filename).stem and path.suffix.lower() in IMAGES | VIDEOS
                    for path in folder.iterdir()
                ):
                    raise ValueError(
                        "No matching image/video exists for this caption. Upload the media first, then retry this caption."
                    )
            else:
                checked = self.store.probe(partial, timeline=True, extension=suffix)
                if not checked["valid"]:
                    raise ValueError(checked.get("error", "Uploaded media cannot be decoded."))
                formats = {
                    ".jpg": "JPEG",
                    ".jpeg": "JPEG",
                    ".png": "PNG",
                    ".webp": "WEBP",
                    ".bmp": "BMP",
                    ".tif": "TIFF",
                    ".tiff": "TIFF",
                    ".gif": "GIF",
                }
                if suffix in formats and checked["metadata"].get("format") != formats[suffix]:
                    raise ValueError(
                        "Uploaded image content does not match its filename extension. Rename or re-encode the original."
                    )
                containers = {
                    ".mp4": {"mov", "mp4"},
                    ".mov": {"mov", "mp4"},
                    ".mkv": {"matroska"},
                    ".webm": {"webm", "matroska"},
                    ".avi": {"avi"},
                    ".wav": {"wav"},
                    ".flac": {"flac"},
                    ".mp3": {"mp3"},
                    ".aac": {"aac"},
                    ".m4a": {"mov", "mp4"},
                    ".ogg": {"ogg"},
                    ".opus": {"ogg"},
                }
                if suffix in containers and not containers[suffix].intersection(
                    str(checked["metadata"].get("format", "")).split(",")
                ):
                    raise ValueError(
                        "Uploaded media container does not match its filename extension. Rename or re-encode the original."
                    )
                if suffix in AUDIO and not checked["metadata"].get("audio"):
                    raise ValueError("Uploaded audio file contains no decodable audio stream.")
            destination = within_root(folder / filename, [folder])
            try:
                os.link(partial, destination)
            except FileExistsError as error:
                if not os.path.samefile(partial, destination):
                    raise CaptionConflict(
                        "A file with this name appeared during upload. Rename the upload; completed files are preserved."
                    ) from error
            record["status"] = "complete"
            atomic_json(self.root / f"{upload_id}.json", record)
            partial.unlink()
        self.store.scan(dataset_id)
        return self.status(dataset_id, upload_id)

    def cancel(self, dataset_id: str, upload_id: str) -> dict[str, Any]:
        with self._lock():
            record = self._record(dataset_id, upload_id)
            if record["status"] != "complete":
                self._partial(dataset_id, upload_id).unlink(missing_ok=True)
                record["status"] = "cancelled"
                atomic_json(self.root / f"{upload_id}.json", record)
            return self.status(dataset_id, upload_id)

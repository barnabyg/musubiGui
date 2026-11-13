"""Batch H3 preparation creates a separate dataset and never replaces source media."""

import json
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from musubi_gui.datasets import DatasetStore, regular_path
from musubi_gui.settings import atomic_json


class H3PreparationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    output_root: str
    width: int = Field(default=640, ge=64, le=2048)
    height: int = Field(default=352, ge=64, le=2048)
    frames: int = Field(default=124, ge=124, le=345)
    fit: Literal["crop", "pad"] = "crop"
    allow_missing_audio: bool = False

    @model_validator(mode="after")
    def geometry(self) -> "H3PreparationRequest":
        if self.width % 32 or self.height % 32 or (self.frames - 5) % 17:
            raise ValueError("Use width/height multiples of 32 and 124–345 frames in increments of 17.")
        return self


class H3Preparation:
    def __init__(self, store: DatasetStore):
        self.store = store
        self.lock = threading.Lock()
        self.active: set[str] = set()

    def _path(self, dataset_id: str) -> Path:
        self.store.get(dataset_id)
        return self.store.root / "h3-preparation" / f"{dataset_id}.json"

    def status(self, dataset_id: str) -> dict[str, Any] | None:
        path = self._path(dataset_id)
        if not path.exists():
            return None
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if record["state"] == "running" and dataset_id not in self.active:
            record["state"] = "interrupted"
            record["error"] = "Preparation was interrupted. Completed copies are preserved; start a new batch to retry."
        return record

    def begin(self, dataset_id: str, request: H3PreparationRequest) -> dict[str, Any]:
        with self.lock:
            if self.active:
                raise ValueError("An H3 preparation batch is already running. Wait for it to finish.")
            source = self.store.folder(dataset_id)
            parent = self.store._folder(request.output_root)
            if parent.is_relative_to(source):
                raise ValueError(
                    "Choose an output parent outside the source dataset, inside a configured dataset root."
                )
            items = [item for item in self.store.items(dataset_id) if item["kind"] == "video" and not item["excluded"]]
            if not items:
                raise ValueError("The dataset has no included videos. Add videos and re-scan first.")
            destination = self.store.create_managed(request.name, str(parent))
            record = {
                "version": 1,
                "state": "running",
                "dataset_id": destination["id"],
                "path": destination["path"],
                "settings": request.model_dump(),
                "completed": 0,
                "total": len(items),
                "sources": [{"id": item["id"], "name": item["name"]} for item in items],
                "items": [],
            }
            path = self._path(dataset_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, record)
            self.active.add(dataset_id)
            return record

    def run(self, dataset_id: str) -> None:
        # begin() validated the library identity. A library entry may have been removed
        # since then; still finalize its report and release the batch slot.
        if not dataset_id.isalnum():
            raise ValueError("Invalid preparation dataset identity.")
        path = self.store.root / "h3-preparation" / f"{dataset_id}.json"
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        request = H3PreparationRequest.model_validate(record["settings"])
        try:
            destination = self.store.folder(record["dataset_id"])
            items = record["sources"]
            for index, item in enumerate(items):
                outcome = {"name": item["name"], "state": "failed", "error": ""}
                # IDs avoid collisions between same-stem source containers and nested folders.
                output = regular_path(destination / f"{Path(item['name']).stem}-{item['id'][:8]}.mp4", destination)
                staging = destination / f".preparing-{item['id']}.mp4"
                try:
                    source = self.store.media_path(dataset_id, item["id"])
                    # Source geometry is precisely what this tool changes. Other media errors must be repaired first.
                    inspection = self.store.inspect_h3(dataset_id, item["id"], request.frames, check_geometry=False)
                    if inspection["errors"]:
                        raise ValueError(" ".join(inspection["errors"]))
                    if inspection["source"] == "missing" and not request.allow_missing_audio:
                        raise ValueError("No audio supplied. Enable Allow videos without audio to include this clip.")
                    caption = self.store.caption(dataset_id, item["id"])
                    command = [
                        sys.executable,
                        "-m",
                        "musubi_gui.h3_prepare_worker",
                        str(source),
                        str(staging),
                        "--width",
                        str(request.width),
                        "--height",
                        str(request.height),
                        "--frames",
                        str(request.frames),
                        "--fit",
                        request.fit,
                    ]
                    if inspection["audio_path"]:
                        audio = regular_path(Path(inspection["audio_path"]), self.store.folder(dataset_id))
                        command.extend(["--audio", str(audio)])
                    # B603: fixed app worker and validated local paths, no shell. One clip has a 15-minute hard limit.
                    converted = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)  # nosec B603
                    if converted.returncode:
                        raise ValueError(converted.stderr.strip()[-2000:] or "Video conversion failed.")
                    if staging.with_suffix(".wav").exists():
                        staging.with_suffix(".wav").rename(output.with_suffix(".wav"))
                    if caption["exists"]:
                        output.with_suffix(".txt").write_text(caption["text"], encoding="utf-8")
                    staging.rename(output)
                    outcome.update(state="complete", output=output.name)
                except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                    outcome["error"] = str(error) or type(error).__name__
                    # Only remove files owned by this conversion, in its fresh managed folder.
                    for partial in [output, output.with_suffix(".wav"), output.with_suffix(".txt")]:
                        partial.unlink(missing_ok=True)
                finally:
                    staging.unlink(missing_ok=True)
                    staging.with_suffix(".wav").unlink(missing_ok=True)
                record["items"].append(outcome)
                record["completed"] = index + 1
                atomic_json(path, record)
            self.store.scan(record["dataset_id"])
            record["state"] = "complete"
        except Exception as error:
            record.update(state="failed", error=str(error))
        finally:
            atomic_json(path, record)
            with self.lock:
                self.active.discard(dataset_id)

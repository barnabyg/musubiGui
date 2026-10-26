"""Exact, recoverable caption batches. A preview never writes a sidecar."""

import json
import sqlite3
import uuid
from contextlib import closing
from typing import Any

from musubi_gui.datasets import CaptionConflict, DatasetStore
from musubi_gui.settings import atomic_json


class DatasetBulk:
    def __init__(self, store: DatasetStore):
        self.store = store
        self.root = store.root / "bulk-captions"

    def _read(self, identifier: str, dataset_id: str, kind: str) -> dict[str, Any]:
        if len(identifier) != 32 or any(value not in "0123456789abcdef" for value in identifier):
            raise ValueError("Invalid bulk operation identity.")
        path = self.root / f"{identifier}-{kind}.json"
        if not path.is_file():
            raise KeyError("Bulk operation was not found. Preview a new selection.")
        value = dict(json.loads(path.read_text(encoding="utf-8")))
        if value.get("version") != 1 or value.get("dataset_id") != dataset_id:
            raise ValueError("Bulk operation belongs to another dataset or an unsupported format; it was preserved.")
        return value

    def preview(
        self, dataset_id: str, item_ids: list[str], prefix: str, suffix: str, find: str, replacement: str
    ) -> dict[str, Any]:
        items = {item["id"]: item for item in self.store.items(dataset_id)}
        if not item_ids or len(item_ids) > 1000 or not set(item_ids).issubset(items):
            raise ValueError("Select 1–1000 existing items from this dataset.")
        changes = []
        for item_id in dict.fromkeys(item_ids):
            caption = self.store.caption(dataset_id, item_id)
            text = caption["text"].replace(find, replacement) if find else caption["text"]
            after = prefix + text + suffix
            if len(after.encode("utf-8")) > 2 * 1024**2:
                raise ValueError(f"{items[item_id]['name']}: resulting caption exceeds 2 MiB.")
            changes.append(
                {
                    "id": item_id,
                    "name": items[item_id]["name"],
                    "before": caption["text"],
                    "after": after,
                    "revision": caption["revision"],
                }
            )
        preview = {"version": 1, "id": uuid.uuid4().hex, "dataset_id": dataset_id, "items": changes}
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_json(self.root / f"{preview['id']}-preview.json", preview)
        return preview

    def apply(self, dataset_id: str, preview_id: str) -> dict[str, Any]:
        preview = self._read(preview_id, dataset_id, "preview")
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / "operation-lock.sqlite3", timeout=30)) as lock, lock:
            lock.execute("CREATE TABLE IF NOT EXISTS mutex (id INTEGER PRIMARY KEY)")
            lock.execute("BEGIN IMMEDIATE")
            path = self.root / f"{preview_id}-operation.json"
            operation: dict[str, Any] = (
                self._read(preview_id, dataset_id, "operation")
                if path.exists()
                else {"version": 1, "id": preview_id, "dataset_id": dataset_id, "items": []}
            )
            done = {item["id"] for item in operation["items"]}
            for change in preview["items"]:
                if change["id"] in done:
                    continue
                item = {"id": change["id"], "name": change["name"], "status": "failed"}
                try:
                    saved = self.store.save_caption(dataset_id, change["id"], change["after"], change["revision"])
                    item.update(status="saved", revision=saved["revision"], history_id=saved["history_id"])
                except CaptionConflict as error:
                    item.update(status="conflict", error=str(error))
                except (OSError, ValueError, KeyError) as error:
                    item.update(error=str(error))
                operation["items"].append(item)
                atomic_json(path, operation)
            return operation

    def restore(self, dataset_id: str, operation_id: str) -> dict[str, Any]:
        operation = self._read(operation_id, dataset_id, "operation")
        results = []
        for item in operation["items"]:
            if item["status"] != "saved":
                continue
            restored = {"id": item["id"], "name": item["name"], "status": "failed"}
            try:
                saved = self.store.restore_caption(dataset_id, item["id"], item["history_id"], item["revision"])
                restored.update(status="restored", revision=saved["revision"])
            except CaptionConflict as error:
                restored.update(status="conflict", error=str(error))
            except (OSError, ValueError, KeyError) as error:
                restored.update(error=str(error))
            results.append(restored)
        report = {"version": 1, "id": operation_id, "dataset_id": dataset_id, "items": results}
        atomic_json(self.root / f"{operation_id}-restoration.json", report)
        return report

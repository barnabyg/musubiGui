"""Dataset API; JSON mutations remain protected by the application's same-origin middleware."""

from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from musubi_gui.datasets import CaptionConflict, DatasetStore
from musubi_gui.datasets_bulk import DatasetBulk

T = TypeVar("T")


def result(action: Callable[[], T]) -> T:
    try:
        return action()
    except CaptionConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(
            500,
            "Dataset operation failed. Check permissions/free disk; source files are preserved and the operation can be retried.",
        ) from error


class RegisterDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    path: str


class CaptionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    revision: str


class CaptionRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history_id: str
    revision: str


class AudioSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class Exclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[str]
    excluded: bool


class BulkPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[str]
    prefix: str = ""
    suffix: str = ""
    find: str = ""
    replace: str = ""


class BulkApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str


class BulkRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str


def dataset_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/datasets")
    store = DatasetStore(root)
    bulk = DatasetBulk(store)

    @router.get("")
    def datasets() -> list[dict[str, Any]]:
        return result(store.list)

    @router.post("")
    def register(request: RegisterDataset, background: BackgroundTasks) -> dict[str, Any]:
        dataset = result(lambda: store.register(request.name, request.path))
        background.add_task(store.scan, dataset["id"])
        return dataset

    @router.get("/{dataset_id}")
    def dataset(dataset_id: str) -> dict[str, Any]:
        return result(lambda: store.get(dataset_id))

    @router.post("/{dataset_id}/scan")
    def scan(dataset_id: str, background: BackgroundTasks) -> dict[str, Any]:
        dataset = result(lambda: store.get(dataset_id))
        background.add_task(store.scan, dataset_id)
        return dataset

    @router.get("/{dataset_id}/items")
    def items(dataset_id: str) -> list[dict[str, Any]]:
        return result(lambda: store.items(dataset_id))

    @router.post("/{dataset_id}/exclusions")
    def exclusions(dataset_id: str, request: Exclusions) -> dict[str, Any]:
        return result(lambda: store.exclude(dataset_id, request.item_ids, request.excluded))

    @router.post("/{dataset_id}/bulk/preview")
    def preview(dataset_id: str, request: BulkPreview) -> dict[str, Any]:
        return result(
            lambda: bulk.preview(
                dataset_id, request.item_ids, request.prefix, request.suffix, request.find, request.replace
            )
        )

    @router.post("/{dataset_id}/bulk/apply")
    def apply(dataset_id: str, request: BulkApply) -> dict[str, Any]:
        return result(lambda: bulk.apply(dataset_id, request.preview_id))

    @router.post("/{dataset_id}/bulk/restore")
    def restore_bulk(dataset_id: str, request: BulkRestore) -> dict[str, Any]:
        return result(lambda: bulk.restore(dataset_id, request.operation_id))

    @router.get("/{dataset_id}/h3")
    def h3_summary(dataset_id: str, frames: int = 124) -> dict[str, Any]:
        def inspect() -> dict[str, Any]:
            items = store.items(dataset_id)
            inspected = [
                {"id": item["id"], "name": item["name"], **store.inspect_h3(dataset_id, item["id"], frames)}
                for item in items
                if item["valid"] and not item["excluded"]
            ]
            supervised = sum(
                1 for item in inspected if item["source"] not in {"missing", "ambiguous"} and not item["errors"]
            )
            return {
                "items": inspected,
                "included": len(inspected),
                "excluded": sum(1 for item in items if item["excluded"]),
                "invalid": sum(1 for item in items if not item["valid"]),
                "supervised": supervised,
                "coverage": supervised / len(inspected) if inspected else 0.0,
            }

        return result(inspect)

    @router.get("/{dataset_id}/items/{item_id}/media")
    def media(dataset_id: str, item_id: str) -> FileResponse:
        path = result(lambda: store.media_path(dataset_id, item_id))
        return FileResponse(path, media_type=store.mime_type(dataset_id, item_id))

    @router.get("/{dataset_id}/items/{item_id}/caption")
    def caption(dataset_id: str, item_id: str) -> dict[str, Any]:
        return result(lambda: store.caption(dataset_id, item_id))

    @router.get("/{dataset_id}/items/{item_id}/h3")
    def inspect_h3(dataset_id: str, item_id: str, frames: int = 124) -> dict[str, Any]:
        return result(lambda: store.inspect_h3(dataset_id, item_id, frames))

    @router.put("/{dataset_id}/items/{item_id}/audio")
    def set_audio(dataset_id: str, item_id: str, request: AudioSource) -> dict[str, Any]:
        return result(lambda: store.set_audio(dataset_id, item_id, request.path))

    @router.get("/{dataset_id}/items/{item_id}/audio")
    def audio(dataset_id: str, item_id: str) -> FileResponse:
        inspection = result(lambda: store.inspect_h3(dataset_id, item_id))
        if not inspection["audio_path"]:
            raise HTTPException(404, "No unambiguous audio source is available.")
        return FileResponse(inspection["audio_path"])

    @router.put("/{dataset_id}/items/{item_id}/caption")
    def save_caption(dataset_id: str, item_id: str, request: CaptionWrite) -> dict[str, Any]:
        return result(lambda: store.save_caption(dataset_id, item_id, request.text, request.revision))

    @router.get("/{dataset_id}/items/{item_id}/caption/history")
    def history(dataset_id: str, item_id: str) -> list[dict[str, Any]]:
        return result(lambda: store.caption_history(dataset_id, item_id))

    @router.post("/{dataset_id}/items/{item_id}/caption/restore")
    def restore(dataset_id: str, item_id: str, request: CaptionRestore) -> dict[str, Any]:
        return result(lambda: store.restore_caption(dataset_id, item_id, request.history_id, request.revision))

    @router.get("/{dataset_id}/items/{item_id}/thumbnail")
    def thumbnail(dataset_id: str, item_id: str) -> FileResponse:
        result(lambda: store.media_path(dataset_id, item_id))
        if not item_id.isalnum():
            raise HTTPException(404, "Thumbnail was not found.")
        path = root / "thumbnails" / f"{item_id}.jpg"
        if not path.is_file():
            raise HTTPException(404, "No thumbnail is available. Re-scan after correcting the media problem.")
        return FileResponse(path, media_type="image/jpeg")

    return router

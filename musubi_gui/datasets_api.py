"""Dataset API; JSON mutations remain protected by the application's same-origin middleware."""

from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from musubi_gui.datasets import CaptionConflict, DatasetStore

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


def dataset_router(root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/datasets")
    store = DatasetStore(root)

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

    @router.get("/{dataset_id}/items/{item_id}/media")
    def media(dataset_id: str, item_id: str) -> FileResponse:
        path = result(lambda: store.media_path(dataset_id, item_id))
        return FileResponse(path, media_type=store.mime_type(dataset_id, item_id))

    @router.get("/{dataset_id}/items/{item_id}/caption")
    def caption(dataset_id: str, item_id: str) -> dict[str, Any]:
        return result(lambda: store.caption(dataset_id, item_id))

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

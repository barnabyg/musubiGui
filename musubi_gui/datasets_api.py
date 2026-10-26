"""Dataset API; JSON mutations remain protected by the application's same-origin middleware."""

from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from musubi_gui.datasets import DatasetStore

T = TypeVar("T")


def result(action: Callable[[], T]) -> T:
    try:
        return action()
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

"""Same-origin job operations; storage and validation live in musubi_gui.jobs."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from musubi_gui.jobs import (
    Conversion,
    Job,
    JobConfig,
    JobDraft,
    JobExport,
    JobStore,
    NamedPreset,
    Preset,
    PresetSelection,
    as_draft,
    default_config,
    export_draft,
    review_job,
)
from musubi_gui.settings import load_settings


class JobUpdate(JobDraft):
    revision: int


class DeleteJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False


@contextmanager
def job_errors() -> Iterator[None]:
    try:
        yield
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except (OSError, sqlite3.Error) as error:
        raise HTTPException(
            500, "Job storage could not be accessed. Check permissions/free disk; your edits remain here."
        ) from error


def jobs_router(root: Path, *, fixture_mode: bool = False) -> APIRouter:
    router = APIRouter(prefix="/api/jobs")
    store = JobStore(root)

    @router.get("")
    def get_jobs() -> list[Job]:
        with job_errors():
            return store.list()

    @router.get("/archived")
    def get_archived() -> list[Job]:
        with job_errors():
            return store.archived()

    @router.get("/defaults/{family}")
    def get_defaults(family: Literal["wan", "h3"]) -> JobConfig:
        return default_config(family)

    @router.get("/presets")
    def get_presets() -> list[Preset]:
        with job_errors():
            return store.presets()

    @router.post("/import")
    def import_job(value: JobExport) -> Job:
        with job_errors():
            return store.create(export_draft(value.draft).draft)

    @router.post("")
    def create_job(draft: JobDraft) -> Job:
        with job_errors():
            return store.create(draft)

    @router.get("/{job_id}")
    def get_job(job_id: str) -> Job:
        with job_errors():
            return store.get(job_id)

    @router.get("/{job_id}/revisions/{revision}")
    def get_revision(job_id: str, revision: int) -> Job:
        with job_errors():
            return store.get(job_id, revision)

    @router.get("/{job_id}/export")
    def export_job(job_id: str) -> JobExport:
        with job_errors():
            return export_draft(as_draft(store.get(job_id)))

    @router.post("/{job_id}/duplicate")
    def duplicate_job(job_id: str) -> Job:
        with job_errors():
            draft = as_draft(store.get(job_id))
            return store.create(draft.model_copy(update={"name": draft.name + " copy"}))

    @router.post("/{job_id}/archive")
    def archive_job(job_id: str) -> Job:
        with job_errors():
            return store.archive_job(job_id)

    @router.post("/{job_id}/restore")
    def restore_job(job_id: str) -> Job:
        with job_errors():
            return store.restore_job(job_id)

    @router.get("/{job_id}/delete-preview")
    def delete_preview(job_id: str) -> dict[str, Any]:
        with job_errors():
            return store.deletion_preview(job_id)

    @router.post("/{job_id}/delete")
    def delete_job(job_id: str, request: DeleteJob) -> dict[str, Any]:
        with job_errors():
            if not request.confirmed:
                raise ValueError("Explicit confirmation is required to permanently delete a job.")
            store.delete_job(job_id)
            return {"deleted": True, "id": job_id}

    @router.post("/{job_id}/presets")
    def post_preset(job_id: str, value: NamedPreset) -> Preset:
        with job_errors():
            return store.create_preset(job_id, value.name)

    @router.post("/{job_id}/apply-preset")
    def apply_preset(job_id: str, selection: PresetSelection) -> Job:
        with job_errors():
            return store.apply_preset(job_id, selection)

    @router.post("/{job_id}/convert")
    def convert_job(job_id: str, conversion: Conversion) -> dict[str, Any] | Job:
        with job_errors():
            return store.convert(job_id, conversion)

    @router.put("/{job_id}")
    def put_job(job_id: str, update: JobUpdate) -> Job:
        with job_errors():
            return store.save(job_id, JobDraft(**update.model_dump(exclude={"revision"})), update.revision)

    @router.post("/{job_id}/review")
    def review(job_id: str) -> dict[str, Any]:
        with job_errors():
            return review_job(root, store.get(job_id), load_settings(root), fixture_mode=fixture_mode)

    return router

"""Job revision files, presets saved beside them, and archiving and deletion."""

import builtins
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException

from musubi_gui.job_models import (
    MEASURED_CONTROLS,
    MEASURED_PRESETS,
    SMOKE_CONTROLS,
    SMOKE_PRESETS,
    Conversion,
    Job,
    JobDraft,
    Preset,
    PresetSelection,
    as_draft,
    conversion_draft,
    export_draft,
)
from musubi_gui.run_store import RunStore, preparation_transaction
from musubi_gui.settings import atomic_json
from musubi_gui.wan_pair import PAIRED_RECIPE

_lock = threading.RLock()


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid job identity.")
    return value


class JobStore:
    def __init__(self, root: Path):
        self.root = root / "jobs"
        # Outside the job folders: revision files stay as written and older versions ignore the markers.
        self.archive_dir = root / "archived-jobs"

    def is_archived(self, job_id: str) -> bool:
        return (self.archive_dir / f"{identifier(job_id)}.json").is_file()

    def _require_unarchived(self, job_id: str) -> None:
        if self.is_archived(job_id):
            raise ValueError("This job is archived. Restore it before changing it.")

    def archive_job(self, job_id: str) -> Job:
        # The preparation gate keeps Prepare from creating a Run between the check and the marker.
        with preparation_transaction(self.root.parent), self._mutation():
            job = self.get(job_id)
            if RunStore(self.root.parent).has_active_run(job_id):
                raise HTTPException(
                    409, "This job has an active Run. Wait for it to finish, or stop or cancel it, before archiving."
                )
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(self.archive_dir / f"{job_id}.json", {"version": 1, "id": job_id})
            return job

    def restore_job(self, job_id: str) -> Job:
        with self._mutation():
            job = self.get(job_id)
            (self.archive_dir / f"{job_id}.json").unlink(missing_ok=True)
            return job

    def deletion_preview(self, job_id: str) -> dict[str, Any]:
        self.get(job_id)
        runs = RunStore(self.root.parent).count_for_job(job_id)
        refusal = (
            f"This job still has {runs} Run(s). Use Delete run on each of them first, or Archive job to hide it instead."
            if runs
            else None
        )
        return {"runs": runs, "refusal": refusal}

    def delete_job(self, job_id: str) -> None:
        # The preparation gate keeps Prepare from creating a Run between the check and the removal.
        with preparation_transaction(self.root.parent), self._mutation():
            refusal = self.deletion_preview(job_id)["refusal"]
            if refusal:
                raise HTTPException(409, refusal)
            directory = self.root / job_id
            # Latest revision last: an interrupted delete leaves the job listed as it was, to delete again.
            files = sorted(directory.iterdir(), key=lambda file: int(file.stem) if file.stem.isdigit() else -1)
            # Any revision this version can't read stays blocked and preserved, not just the latest.
            for file in files:
                if file.stem.isdigit():
                    self.get(job_id, int(file.stem))
            for file in files:
                file.unlink()
            directory.rmdir()
            (self.archive_dir / f"{job_id}.json").unlink(missing_ok=True)

    def archived(self) -> builtins.list[Job]:
        return [job for job in self._stored() if self.is_archived(job.id)]

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        """Keep revision read/check/write atomic across backend processes sharing this state."""
        self.root.parent.mkdir(parents=True, exist_ok=True)
        with _lock, closing(sqlite3.connect(self.root.parent / "job-revisions-lock.sqlite3", timeout=30)) as lock, lock:
            lock.execute("BEGIN IMMEDIATE")
            yield

    def get(self, job_id: str, revision: int | None = None) -> Job:
        directory = self.root / identifier(job_id)
        files = []
        for file in directory.iterdir() if directory.is_dir() else []:
            if file.name.startswith(".pending-") and file.suffix == ".json" and not file.is_dir():
                # Atomic staging files may disappear during discovery; they are never revisions.
                continue
            if not file.is_file() or file.suffix != ".json" or not file.stem.isdigit():
                raise ValueError("Unrecognised job storage; preserve the files before repair.")
            files.append(file)
        if not files:
            raise FileNotFoundError("Job not found.")
        number = revision if revision is not None else max(int(file.stem) for file in files)
        job = Job.model_validate_json((directory / f"{number}.json").read_text(encoding="utf-8"))
        if job.id != job_id or job.revision != number:
            raise ValueError("Stored job identity/revision does not match its location; preserve it before repair.")
        return job

    def list(self) -> list[Job]:
        return [job for job in self._stored() if not self.is_archived(job.id)]

    def summaries(self) -> builtins.list[dict[str, Any]]:
        """What the job list shows beyond the revision: when it was last saved and how its last Run went."""
        latest: dict[str, dict[str, Any]] = {}
        for run in sorted(RunStore(self.root.parent).list(), key=lambda run: run["created"]):
            latest[run["job"]["id"]] = {"id": run["id"], "status": run["status"]}
        return [
            {
                "id": job.id,
                # The revision file's time: revision files stay as written, with no time field.
                "edited": datetime.fromtimestamp(
                    (self.root / job.id / f"{job.revision}.json").stat().st_mtime, UTC
                ).isoformat(),
                "last_run": latest.get(job.id),
            }
            for job in self.list()
        ]

    def _stored(self) -> builtins.list[Job]:
        jobs = []
        for directory in sorted(self.root.glob("*")):
            if not directory.is_dir():
                continue
            try:
                jobs.append(self.get(directory.name))
            except FileNotFoundError:
                # The first revision has not been published, or disappeared during discovery.
                continue
        return jobs

    def create(self, draft: JobDraft) -> Job:
        with _lock:
            job = Job(**draft.model_dump(), id=uuid.uuid4().hex, revision=1)
            directory = self.root / job.id
            directory.mkdir(parents=True)
            atomic_json(directory / "1.json", job.model_dump())
            return job

    def save(self, job_id: str, draft: JobDraft, expected_revision: int) -> Job:
        with self._mutation():
            old = self.get(job_id)
            self._require_unarchived(job_id)
            if expected_revision != old.revision:
                raise HTTPException(
                    409, "This job changed in another tab. Reopen it before saving; your edits remain here."
                )
            if draft.family != old.family:
                raise ValueError("Changing the model requires the explicit conversion action.")
            job = Job(**draft.model_dump(), id=old.id, revision=old.revision + 1)
            atomic_json(self.root / old.id / f"{job.revision}.json", job.model_dump())
            return job

    def convert(self, job_id: str, conversion: Conversion) -> dict[str, Any] | Job:
        with self._mutation():
            old = self.get(job_id)
            self._require_unarchived(job_id)
            if conversion.revision != old.revision:
                raise HTTPException(409, "The draft changed. Reopen it and preview the conversion again.")
            if conversion.family == old.family:
                raise ValueError("Select a different model for conversion.")
            draft = conversion_draft(old, conversion.family)
            if not conversion.confirmed:
                return {
                    "draft": draft.model_dump(),
                    "changes": [
                        {"field": key, "before": value, "after": draft.config.model_dump()[key]}
                        for key, value in old.config.model_dump().items()
                        if draft.config.model_dump()[key] != value
                    ],
                    "message": "Model/task-specific recipe, components, frame count and memory options are replaced. Shared controls and the previous revision are retained. Review the converted draft before preparing.",
                }
            job = Job(**draft.model_dump(), id=old.id, revision=old.revision + 1)
            atomic_json(self.root / old.id / f"{job.revision}.json", job.model_dump())
            return job

    def presets(self) -> builtins.list[Preset]:
        saved = [
            Preset.model_validate_json(file.read_text(encoding="utf-8"))
            for file in sorted((self.root.parent / "presets").glob("*.json"))
        ]
        return [
            *MEASURED_PRESETS.values(),
            *SMOKE_PRESETS.values(),
            *(preset for preset in saved if preset.family != "wan" or preset.config.recipe == PAIRED_RECIPE),
        ]

    def create_preset(self, job_id: str, name: str) -> Preset:
        job = self.get(job_id)
        self._require_unarchived(job_id)
        preset = Preset(
            name=name,
            id=uuid.uuid4().hex,
            family=job.family,
            config=export_draft(as_draft(job)).draft.config,
            dataset_resolutions={entry.dataset_id: entry.resolution for entry in job.datasets},
        )
        directory = self.root.parent / "presets"
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / f"{preset.id}.json", preset.model_dump())
        return preset

    def apply_preset(self, job_id: str, selection: PresetSelection) -> Job:
        with _lock:
            job = self.get(job_id)
            built_in = MEASURED_PRESETS.get(selection.preset_id) or SMOKE_PRESETS.get(selection.preset_id)
            if built_in:
                preset = built_in
            else:
                identifier(selection.preset_id)
                preset = Preset.model_validate_json(
                    (self.root.parent / "presets" / f"{selection.preset_id}.json").read_text(encoding="utf-8")
                )
                if preset.id != selection.preset_id:
                    raise ValueError("Preset identity mismatch; preserve its file before repair.")
            if job.family != preset.family:
                raise ValueError("This preset belongs to another model. Preview and accept a model conversion first.")
            controls = SMOKE_CONTROLS if selection.preset_id in SMOKE_PRESETS else MEASURED_CONTROLS
            config = (
                job.config.model_copy(update={key: getattr(preset.config, key) for key in controls})
                if built_in
                else preset.config
            )
            draft = as_draft(job).model_copy(update={"config": config})
            if not built_in and preset.dataset_resolutions:
                draft.datasets = [
                    entry.model_copy(update={"resolution": preset.dataset_resolutions[entry.dataset_id]})
                    if entry.dataset_id in preset.dataset_resolutions
                    else entry
                    for entry in job.datasets
                ]
            return self.save(job_id, draft, selection.revision)


def load_job(root: Path, job_id: str) -> Job:
    return JobStore(root).get(job_id)


def list_jobs(root: Path) -> list[Job]:
    return JobStore(root).list()

"""Versioned job drafts and deterministic plans for the pinned LoRA recipe."""

import builtins
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from musubi_gui.diagnostics import upstream_revision
from musubi_gui.disk_estimate import check_volumes, estimate_disk, free_space_reader
from musubi_gui.h3 import add_image_flags, h3_stages, validate_h3
from musubi_gui.recipe import CAPTION, UPSTREAM, Stage
from musubi_gui.redaction import redact
from musubi_gui.run_paths import job_folder
from musubi_gui.run_store import RunStore, preparation_transaction
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.sampling import native_preview_frames, sample_geometry
from musubi_gui.settings import Settings, atomic_json, validate_paths, within_root
from musubi_gui.training_sections import dataset_resolution, dataset_toml, plan_sections, section_balance
from musubi_gui.video_sampling import plan_windows
from musubi_gui.wan import validate_wan, wan_stages
from musubi_gui.wan_pair import MAX_PHASES, PAIRED_RECIPE, alternating_phases, expert_steps

_lock = threading.RLock()

# Pinned UPSTREAM src/musubi_tuner/dataset/media_utils.py. JPEG-XL is optional
# upstream and is not indexed by this application's gallery.
TRAINING_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif"})
TRAINING_VIDEOS = frozenset({".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"})
# Highest block-swap values that trained on the target PC; the next value up fails (docs/reference).
WAN_MAX_BLOCKS_TO_SWAP = 38
H3_MAX_BLOCKS_TO_SWAP = 48
# Below this a LoRA has made no visible change to samples in the owner's experience on the target PC.
LOW_STEPS_WARNING = 600
H3_IMAGE_WARNINGS = (
    "H3 images: upstream calls this one-frame training experimental, and using the result for video is unvalidated. "
    "Short image-only and mixed runs completed on the target PC (about 5–6 s per image step, 85,980 bytes per image cache "
    "at 640×352), which proves mechanics and not learning.",
    "H3 images: upstream advises stating the absence of sound in each image's caption, such as a sound: field describing "
    "a silent still. Captions belong to the dataset, so editing them affects every other model using it. This application doesn't change them.",
)
# Above this share one section can swamp the others without notice, such as 200 images beside 10 clips.
BALANCE_WARNING_SHARE = 0.8
# A shape this close to the job's loses too little to a crop to be worth a warning.
ASPECT_TOLERANCE = 0.01
NEW_DRAFT_STEPS = 600


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    recipe: str = "low-noise"
    expert_interval: int = 30
    components: dict[str, str] = Field(default_factory=dict)
    width: int = 256
    height: int = 256
    frames: int = 17
    # Pinned UPSTREAM frame_extraction modes qualified here; `full` has unbounded clip length and memory.
    frame_extraction: Literal["head", "chunk", "slide", "uniform"] = "head"
    frame_stride: int = 16
    frame_sample: int = 4
    batch_size: int = 1
    repeats: int = 1
    enable_bucket: bool = False
    bucket_no_upscale: bool = False
    latent_cache_policy: Literal["reuse", "rebuild"] = "reuse"
    text_cache_policy: Literal["reuse", "rebuild"] = "reuse"
    rank: int = 8
    alpha: int = 8
    learning_rate: float = 0.0001
    optimizer: Literal["AdamW", "AdamW8bit"] = "AdamW"
    timestep_sampling: Literal["shift", "sigmoid", "uniform"] = "shift"
    sigmoid_scale: float = 1.0
    discrete_flow_shift: float = 3.0
    steps: int = 2
    gradient_accumulation: int = 1
    blocks_to_swap: int = 36
    gradient_checkpointing: bool = True
    fp8_scaled: bool = True
    offload_inactive_dit: bool = False
    block_swap_h2d_only: bool = False
    audio_policy: Literal["require", "allow-missing"] = "require"
    save_every: int = 1
    keep_checkpoints: int = 0
    save_state: bool = False
    sample_enabled: bool = True
    preview_every: int = 0
    sample_prompt: str = CAPTION
    sample_steps: int = 4
    # None preserves the resolved geometry of released version-one Jobs and exports.
    sample_width: int | None = Field(default=None, exclude_if=lambda value: value is None)
    sample_height: int | None = Field(default=None, exclude_if=lambda value: value is None)
    sample_frames: int | None = Field(default=None, exclude_if=lambda value: value is None)
    seed: int = 42


def smoke_config(family: str) -> JobConfig:
    """The quick execution check, which proves mechanics and not learning.

    H3 fields missing from saved revisions and exports resolve to these values, so they must stay fixed.
    """
    if family == "h3":
        return JobConfig(
            recipe="guidance-loss",
            timestep_sampling="uniform",
            frames=124,
            rank=4,
            alpha=4,
            blocks_to_swap=48,
            fp8_scaled=False,
            block_swap_h2d_only=True,
            sample_prompt=CAPTION + " A steady sine tone plays.",
        )
    return JobConfig()


def default_config(family: str) -> JobConfig:
    """Where a new draft starts: the capacity measured in #26, long enough to learn, and no placeholder prompt."""
    size: dict[str, Any] = (
        {"width": 640, "height": 352, "rank": 8, "alpha": 8, "sample_steps": 10}
        if family == "h3"
        else {"width": 832, "height": 480, "frames": 33, "rank": 16, "alpha": 16, "sample_steps": 20}
    )
    return smoke_config(family).model_copy(
        update={
            **size,
            **({"recipe": PAIRED_RECIPE} if family == "wan" else {}),
            "steps": NEW_DRAFT_STEPS,
            "save_every": 100,
            "save_state": True,
            "sample_prompt": "",
        }
    )


class JobDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    # None inherits config.repeats, the [general] default.
    repeats: int | None = None
    # Omit absent overrides so older records and exports keep their original shape.
    resolution: tuple[int, int] | None = Field(default=None, exclude_if=lambda value: value is None)


def _check_version_fields(version: Any, draft: Any) -> None:
    """A version names one shape: 1 has a single dataset_id and 2 has the datasets list."""
    if isinstance(draft, dict) and ((version == 1 and "datasets" in draft) or (version == 2 and "dataset_id" in draft)):
        raise ValueError(f"Version {version} does not have this dataset field; preserve the file before repair.")


class JobDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Untitled job"
    family: Literal["wan", "h3"] = "wan"
    datasets: list[JobDataset] = Field(default_factory=list)
    config: JobConfig = Field(default_factory=JobConfig)

    @model_validator(mode="before")
    @classmethod
    def legacy_dataset(cls, value: Any) -> Any:
        """Version 1 revisions and exports, and API requests that still send dataset_id."""
        if not isinstance(value, dict) or "dataset_id" not in value:
            return value
        if "datasets" in value:
            raise ValueError("Send either dataset_id or datasets, not both.")
        rest = {key: child for key, child in value.items() if key != "dataset_id"}
        legacy = value["dataset_id"]
        return {**rest, "datasets": [{"dataset_id": legacy}] if legacy else []}

    @model_validator(mode="before")
    @classmethod
    def family_defaults(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("family") == "h3":
            supplied = value.get("config", {})
            if isinstance(supplied, dict):
                return {**value, "config": {**smoke_config("h3").model_dump(), **supplied}}
        return value


class Job(JobDraft):
    version: Literal[2] = 2
    id: str
    revision: int

    @model_validator(mode="before")
    @classmethod
    def read_version_1(cls, value: Any) -> Any:
        """Revision files stay as written; a version 1 file reads as the version 2 job it now means."""
        if not isinstance(value, dict) or "version" not in value:
            return value
        _check_version_fields(value["version"], value)
        return {**value, "version": 2} if value["version"] == 1 else value


class JobExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1, 2]
    format: Literal["musubi-gui-job"]
    draft: JobDraft

    @model_validator(mode="before")
    @classmethod
    def version_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            _check_version_fields(value.get("version"), value.get("draft"))
        return value


class NamedPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class Preset(NamedPreset):
    version: Literal[1] = 1
    id: str
    family: Literal["wan", "h3"]
    config: JobConfig
    dataset_resolutions: dict[str, tuple[int, int] | None] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )

    @model_validator(mode="before")
    @classmethod
    def legacy_h3_timestep(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("family") == "h3":
            config = value.get("config")
            if isinstance(config, dict) and "timestep_sampling" not in config:
                return {**value, "config": {**config, "timestep_sampling": "uniform"}}
        return value


# Capacity configurations measured on the target PC in #26; see docs/reference/evidence/production-trial.json.
# Applying one replaces only these memory-bound controls, so model components, prompt, steps,
# learning rate and checkpoint cadence stay as the job has them.
MEASURED_CONTROLS = (
    "width",
    "height",
    "frames",
    "batch_size",
    "rank",
    "alpha",
    "blocks_to_swap",
    "gradient_checkpointing",
    "fp8_scaled",
    "offload_inactive_dit",
    "block_swap_h2d_only",
)
MEASURED_PRESETS = {
    preset.id: preset
    for preset in [
        Preset(
            id="measured-wan-832x480x33-r16",
            name="Measured · Wan 832×480 · 33 frames · rank 16",
            family="wan",
            config=smoke_config("wan").model_copy(
                update={"width": 832, "height": 480, "frames": 33, "rank": 16, "alpha": 16, "blocks_to_swap": 36}
            ),
        ),
        Preset(
            id="measured-h3-640x352x124-r8",
            name="Measured · H3 640×352 · 124 frames · rank 8",
            family="h3",
            config=smoke_config("h3").model_copy(update={"width": 640, "height": 352, "rank": 8, "alpha": 8}),
        ),
    ]
}
# Applying one replaces everything that decides how long the check takes, and keeps components, learning rate and seed.
SMOKE_CONTROLS = (
    *MEASURED_CONTROLS,
    "recipe",
    "expert_interval",
    "steps",
    "save_every",
    "save_state",
    "sample_enabled",
    "preview_every",
    "sample_prompt",
    "sample_steps",
    "sample_width",
    "sample_height",
    "sample_frames",
)
SMOKE_PRESETS = {
    preset.id: preset
    for preset in [
        Preset(
            id="smoke-wan-paired",
            name="Smoke test · Wan alternating · 4 steps",
            family="wan",
            config=smoke_config("wan").model_copy(
                update={"recipe": PAIRED_RECIPE, "expert_interval": 1, "steps": 4, "save_state": True}
            ),
        ),
        Preset(id="smoke-h3", name="Smoke test · H3 · 2 steps", family="h3", config=smoke_config("h3")),
    ]
}


class PresetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str
    revision: int


class Conversion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal["wan", "h3"]
    revision: int
    confirmed: bool = False


def as_draft(job: Job) -> JobDraft:
    return JobDraft.model_validate(job.model_dump(exclude={"id", "version", "revision"}))


def export_draft(draft: JobDraft) -> JobExport:
    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {key: scrub(child) for key, child in value.items()}
        if isinstance(value, list):
            return [scrub(child) for child in value]
        return value

    return JobExport(version=2, format="musubi-gui-job", draft=JobDraft.model_validate(scrub(draft.model_dump())))


def conversion_draft(job: Job, family: Literal["wan", "h3"]) -> JobDraft:
    shared = {
        key: value
        for key, value in job.config.model_dump().items()
        if key
        in {
            "batch_size",
            "repeats",
            "enable_bucket",
            "bucket_no_upscale",
            "learning_rate",
            "steps",
            "gradient_accumulation",
            "gradient_checkpointing",
            "save_every",
            "save_state",
            "sample_enabled",
            "preview_every",
            "sample_prompt",
            "sample_steps",
            "seed",
        }
    }
    # Values still at the source model's new-draft default move to the target's: one model's measured
    # capacity is not the other's, and Wan's 832×480 runs H3 out of VRAM. Values the user chose are kept.
    source = default_config(job.family)
    for key in ["rank", "alpha"]:
        if getattr(job.config, key) != getattr(source, key):
            shared[key] = getattr(job.config, key)
    if (job.config.width, job.config.height) != (source.width, source.height):
        for key in ["width", "height"]:
            value = getattr(job.config, key)
            if value >= 64 and value % (32 if family == "h3" else 16) == 0:
                shared[key] = value
    config = default_config(family).model_copy(update=shared)
    if family == "wan":
        config = config.model_copy(
            update={"save_state": True, "expert_interval": min(config.expert_interval, max(1, config.steps // 2))}
        )
    return JobDraft(name=job.name, family=family, datasets=job.datasets, config=config)


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


def effective_settings(job: Job, settings: Settings) -> Settings:
    return settings.model_copy(update={"models": {**settings.models, **job.config.components}})


def configuration_errors(job: Job, *, has_videos: bool = True) -> list[str]:
    config = job.config
    sampling = config.sample_enabled or config.preview_every > 0
    needs_frames = has_videos
    errors: list[str] = []
    if sampling:
        multiple = 16 if job.family == "wan" else 32
        for dimension in ("width", "height"):
            value = getattr(config, "sample_" + dimension)
            if value is not None and (value < 64 or value % multiple):
                errors.append(f"Sample {dimension}: enter at least 64, in multiples of {multiple}.")
        frames = (
            config.sample_frames if config.sample_frames is not None else (config.frames if not has_videos else None)
        )
        if frames is not None:
            if job.family == "wan" and (frames < 1 or (frames - 1) % 4):
                errors.append("Sample duration: Wan requires 4*n+1 frames, or one frame for an image sample.")
            elif job.family == "h3" and (not 124 <= frames <= 345 or (frames - 5) % 17):
                errors.append("Sample duration: H3 requires 17*n+5 frames between 124 and 345 (24 fps).")
    if job.family == "h3" and config.timestep_sampling != "uniform":
        errors.append("Timestep strategy: the pinned H3 trainer supports uniform sampling only.")
    if job.family == "wan":
        if config.timestep_sampling in {"shift", "sigmoid"} and not 0.001 <= config.sigmoid_scale <= 1000:
            errors.append("Sigmoid scale: enter a finite number from 0.001 to 1000 for stable sampling.")
        if config.timestep_sampling == "shift" and not 0.001 <= config.discrete_flow_shift <= 1000:
            errors.append("Flow shift: enter a finite number from 0.001 to 1000 for stable sampling.")
    if not job.name.strip():
        errors.append("Name: enter a job name.")
    listed = [entry.dataset_id for entry in job.datasets]
    if not listed:
        errors.append("Dataset: select a registered dataset.")
    if len(set(listed)) != len(listed):
        errors.append("Dataset: the same dataset is listed more than once; remove the extra row.")
    if job.family == "wan":
        if config.recipe not in {"low-noise", PAIRED_RECIPE}:
            errors.append("Recipe: choose alternating high/low Wan training.")
        if config.recipe == PAIRED_RECIPE:
            if config.expert_interval < 1:
                errors.append("Expert interval: enter a positive integer.")
            elif math.ceil(config.steps / config.expert_interval) > MAX_PHASES:
                errors.append(f"Expert interval: increase it to keep the Run within {MAX_PHASES} phases.")
            elif config.steps <= config.expert_interval:
                errors.append(
                    "Steps: paired training must include at least one update of each expert; increase steps or reduce the expert interval."
                )
            if not config.save_state:
                errors.append("Training state: alternating experts requires saved optimizer/training state.")
        if config.width < 64 or config.height < 64 or config.width % 16 or config.height % 16:
            errors.append("Geometry: Wan width and height must be at least 64 and multiples of 16.")
        if needs_frames and (config.frames < 1 or (config.frames - 1) % 4):
            errors.append("Frames: Wan requires 4*n+1 frames.")
        if not 0 <= config.blocks_to_swap <= WAN_MAX_BLOCKS_TO_SWAP:
            errors.append(
                f"Block swapping: select between 0 and {WAN_MAX_BLOCKS_TO_SWAP} Wan blocks. "
                "39 fails at step 0 in the pinned upstream FP8 linear patch."
            )
        if not config.fp8_scaled:
            errors.append("Memory: runtime FP8 scaling from native FP16 weights is required by this Wan recipe.")
        if config.block_swap_h2d_only:
            errors.append("Memory: H2D-only swapping belongs to the H3 recipe, not the initial Wan recipe.")
        if has_videos and config.frame_extraction == "slide" and config.frame_stride < 1:
            errors.append("Frame extraction: enter a slide stride of at least one frame.")
        if has_videos and config.frame_extraction == "uniform" and config.frame_sample < 2:
            errors.append("Frame extraction: uniform sampling needs at least two windows per clip; use head for one.")
        allowed_components = {"wan_low", "wan_high", "wan_vae", "wan_t5"}
    else:
        if config.recipe != "guidance-loss":
            errors.append(
                "Recipe: initial H3 T2VA supports guidance loss only; other recipes need separate validation."
            )
        if config.width < 64 or config.height < 64 or config.width % 32 or config.height % 32:
            errors.append("Geometry: H3 width and height must be at least 64 and multiples of 32.")
        if needs_frames and (not 124 <= config.frames <= 345 or (config.frames - 5) % 17):
            errors.append("Frames: H3 requires 17*n+5 frames in the released 124–345 range.")
        if not 0 <= config.blocks_to_swap <= H3_MAX_BLOCKS_TO_SWAP:
            errors.append(
                f"Block swapping: select between 0 and {H3_MAX_BLOCKS_TO_SWAP} of the 50 H3 blocks; "
                "the pinned upstream rejects higher values."
            )
        if config.fp8_scaled:
            errors.append("Memory: H3 does not support FP8 transformer weights/runtime scaling.")
        if not config.block_swap_h2d_only:
            errors.append("Memory: H3 guidance loss requires H2D-only swapping for the validated target-PC recipe.")
        if has_videos and config.frame_extraction != "head":
            errors.append(
                "Frame extraction: H3 uses head only. Audio alignment and coverage are inspected for each clip's first window, so other modes are not qualified."
            )
        allowed_components = {"h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"}
    if config.batch_size != 1:
        errors.append(
            "Batch size: the target-PC recipe supports one; use gradient accumulation for effective batch size."
        )
    for field, value in [
        ("repeats", config.repeats),
        ("rank", config.rank),
        ("alpha", config.alpha),
        ("steps", config.steps),
        ("gradient accumulation", config.gradient_accumulation),
        ("checkpoint interval", config.save_every),
        ("sample steps", config.sample_steps),
    ]:
        if value < 1:
            errors.append(f"{field.capitalize()}: enter a positive integer.")
    if not math.isfinite(config.learning_rate) or not 0 < config.learning_rate <= 1:
        errors.append("Learning rate: enter a finite number greater than zero and at most one.")
    if not config.gradient_checkpointing:
        errors.append("Memory: gradient checkpointing is required by this target-PC recipe.")
    if config.offload_inactive_dit:
        errors.append(
            "Memory: inactive-DiT offload is outside this single-expert recipe and conflicts with block swapping."
        )
    if config.preview_every < 0:
        errors.append("Preview interval: enter zero (off) or a positive number of optimizer steps.")
    if config.keep_checkpoints < 0:
        errors.append("Checkpoint retention: enter zero (unlimited) or a positive number of saves.")
    if config.preview_every > 0 and config.recipe == PAIRED_RECIPE and config.steps > 0 and config.expert_interval > 0:
        try:
            alternating_phases(config.steps, config.expert_interval, preview_every=config.preview_every)
        except ValueError as error:
            errors.append(str(error))
    if (config.sample_enabled or config.preview_every) and not config.sample_prompt.strip():
        errors.append("Sampling: enter a prompt or turn off both final sampling and in-run previews.")
    if any(key not in allowed_components for key in config.components):
        errors.append("Components: only the four supported component keys for the selected model are accepted.")
    return errors


def _set_argument(args: list[str], flag: str, value: str) -> None:
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args.extend([flag, value])


def build_stages(job: Job, settings: Settings, run: Path, cache: Path, kinds: set[str]) -> list[Stage]:
    """Kinds are the included item kinds, because H3 images change its commands."""
    from musubi_gui.training_options import timestep_range_policy

    settings = effective_settings(job, settings)
    config = job.config
    recipe_stages = wan_stages if job.family == "wan" else h3_stages
    sample_width, sample_height, sample_frames = sample_geometry(config)
    stages = [stage for stage in recipe_stages(settings, run, cache) if stage.name != "fixture"]
    if job.family == "h3":
        add_image_flags(stages, kinds)
    for stage in stages:
        args = stage.args
        if stage.name == "train":
            for flag, value in [
                ("--network_dim", config.rank),
                ("--network_alpha", config.alpha),
                ("--learning_rate", config.learning_rate),
                ("--optimizer_type", config.optimizer),
                ("--max_train_steps", config.steps),
                ("--gradient_accumulation_steps", config.gradient_accumulation),
                ("--blocks_to_swap", config.blocks_to_swap),
                ("--save_every_n_steps", config.save_every),
                ("--seed", config.seed),
                ("--output_name", f"{job.family}-{job.id}"),
            ]:
                _set_argument(args, flag, str(value))
            if job.family == "wan":
                _set_argument(args, "--timestep_sampling", config.timestep_sampling)
                if config.timestep_sampling in {"shift", "sigmoid"}:
                    _set_argument(args, "--sigmoid_scale", str(config.sigmoid_scale))
                if config.timestep_sampling == "shift":
                    _set_argument(args, "--discrete_flow_shift", str(config.discrete_flow_shift))
                elif "--discrete_flow_shift" in args:
                    index = args.index("--discrete_flow_shift")
                    del args[index : index + 2]
                # Keep the original qualified recipe exact; custom distributions use bounded mapping.
                # Upstream rejection sampling falls back outside the expert range after 1000 misses.
                if timestep_range_policy(job) != "original" and "--preserve_distribution_shape" in args:
                    args.remove("--preserve_distribution_shape")
            if config.save_state:
                args.extend(["--save_state", "--save_state_on_train_end"])
            if config.preview_every and config.recipe != PAIRED_RECIPE:
                from musubi_gui.preview import preview_arguments

                args.extend(preview_arguments(job, settings, run))
        if stage.name == "generate":
            if job.family == "wan" and config.sample_frames == 1:
                _set_argument(args, "--output_type", "images")
            _set_argument(args, "--prompt", config.sample_prompt)
            _set_argument(args, "--infer_steps", str(config.sample_steps))
            _set_argument(args, "--seed", str(config.seed))
            _set_argument(args, "--blocks_to_swap", str(config.blocks_to_swap))
            _set_argument(args, "--video_length", str(sample_frames))
            index = args.index("--video_size")
            args[index + 1 : index + 3] = [str(sample_height), str(sample_width)]
            _set_argument(args, "--lora_weight", str(run / "checkpoints" / f"{job.family}-{job.id}.safetensors"))
    stages = [
        stage
        for stage in stages
        if stage.name != "generate"
        or config.sample_enabled
        or (config.recipe == PAIRED_RECIPE and config.preview_every > 0)
    ]
    if job.family == "wan" and config.recipe == PAIRED_RECIPE:
        from musubi_gui.wan_pair_plan import paired_stages

        return paired_stages(
            stages,
            settings.models,
            config.steps,
            config.expert_interval,
            preview_every=config.preview_every,
            final_sample=config.sample_enabled,
        )
    return stages


def disk_preflight(
    job: Job,
    settings: Settings,
    items: list[dict[str, Any]],
    video_windows: list[dict[str, Any]],
    *,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    media_bytes = 0
    for item in items:
        try:
            media_bytes += Path(item["absolute_path"]).stat().st_size
        except OSError:
            # Review reports unreadable media through the dataset checks.
            continue
    images = [item for item in items if item["kind"] == "image"]
    sizes = {item["id"]: dataset_resolution(job, item.get("dataset_id", "")) for item in items}
    estimated_items = [
        {**item, "resolution": sizes.get(item["id"], (job.config.width, job.config.height))}
        for item in [*images, *video_windows]
    ]
    estimate = estimate_disk(job.family, job.config, estimated_items, media_bytes=media_bytes)
    volumes = check_volumes(settings, estimate, free=free_space_reader(fixture_mode=fixture_mode))
    return {"estimate": estimate, "volumes": volumes}


class ReviewMessages(list[str]):
    """Text stays compatible with API clients; targets are indexed so identical messages can link to different items."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__(messages)
        self.targets: dict[int, dict[str, str | list[dict[str, str]]]] = {}

    def add(self, message: str, **target: str | list[dict[str, str]]) -> None:
        self.targets[len(self)] = target
        self.append(message)


def _review_items(
    store: Any,
    job: Job,
    dataset: dict[str, Any],
    prefix: str,
    errors: ReviewMessages,
    warnings: ReviewMessages,
    audio_coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Check one listed dataset's included items; returns them labelled with the dataset they came from."""
    dataset_id = dataset["id"]
    items = [
        {**item, "dataset_id": dataset_id} for item in store.items(dataset_id) if not item["excluded"] and item["valid"]
    ]
    if not items:
        errors.add(f'Dataset "{dataset["name"]}": no valid, included media remain.', dataset_id=dataset_id)
    for item in items:
        name = prefix + item["name"]
        target_item = {"dataset_id": dataset_id, "item_id": item["id"]}
        suffix = Path(item["path"]).suffix.lower()
        supported = TRAINING_IMAGES if item["kind"] == "image" else TRAINING_VIDEOS
        if suffix not in supported:
            target = "PNG, JPEG, WebP or BMP" if item["kind"] == "image" else "MP4"
            errors.add(
                f"{name}: {suffix} {item['kind']} files can be previewed but are not supported by the pinned {job.family.upper()} training recipe. Convert to {target} or exclude this item before preparation.",
                view="preview",
                **target_item,
            )
        if not item["caption"]["exists"]:
            warnings.add(
                f"{name}: no caption sidecar; the frozen caption will be empty.", view="caption", **target_item
            )
        frames = item.get("metadata", {}).get("frames")
        # Wan duration is checked after resampling to 16 fps, in plan_windows.
        if job.family == "h3" and item["kind"] == "video" and isinstance(frames, int) and frames < job.config.frames:
            errors.add(
                f"{name}: {frames} decoded frames cannot supply the requested {job.config.frames}; shorten the job or choose a longer video.",
                view="preview",
                **target_item,
            )
        if job.family == "h3" and item["kind"] == "video":
            audio_coverage["included"] += 1
            try:
                inspection: dict[str, Any] = store.inspect_h3(dataset_id, item["id"], frames=job.config.frames)
            except (ValueError, OSError, HTTPException) as error:
                errors.add(f"{name}: {error}", view="audio", **target_item)
                continue
            audio_coverage["items"].append({"id": item["id"], "name": item["name"], **inspection})
            for message in inspection.get("errors", []):
                errors.add(f"{name}: {message}", view="audio", **target_item)
            for message in inspection.get("warnings", []):
                warnings.add(f"{name}: {message}", view="audio", **target_item)
            if inspection.get("source") == "missing":
                audio_coverage["missing"] += 1
                if job.config.audio_policy == "require":
                    errors.add(
                        f"{name}: audio policy requires supervised audio; supply valid aligned audio or allow missing audio explicitly.",
                        view="audio",
                        **target_item,
                    )
                else:
                    warnings.add(
                        f"{name}: absent audio is allowed and excluded from audio supervision; no audio is synthesized.",
                        view="audio",
                        **target_item,
                    )
            elif not inspection.get("errors"):
                audio_coverage["supervised"] += 1
    warnings.extend(_reshaped_image_warning(job, dataset, items))
    return items


def _reshaped_image_warning(job: Job, dataset: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """Bucketing off leaves the pinned loader one bucket: image_video_dataset.py scales to cover it and centre-crops."""
    config = job.config
    width, height = dataset_resolution(job, dataset["id"])
    if config.enable_bucket or width < 1 or height < 1:
        return []
    target = width / height

    def reshaped(item: dict[str, Any]) -> bool:
        width, height = item.get("metadata", {}).get("width"), item.get("metadata", {}).get("height")
        return bool(width and height) and abs(width / height / target - 1) > ASPECT_TOLERANCE

    count = sum(1 for item in items if item["kind"] == "image" and reshaped(item))
    if not count:
        return []
    images = "1 image has" if count == 1 else f"{count} images have"
    return [
        f'Dataset "{dataset["name"]}": {images} a different shape from {width}×{height} and bucketing is '
        "off, so training scales each to cover that size and crops the overflow from the centre. "
        + (
            "Enable bucketing to keep the whole image."
            if job.family == "wan"
            else "Bucketing with H3 images has not been tried on the target PC, so crop or resize them to this shape instead."
        )
    ]


def _plan_job_windows(
    job: Job,
    datasets: list[dict[str, Any]],
    items: list[dict[str, Any]],
    errors: ReviewMessages,
    warnings: ReviewMessages,
) -> dict[str, Any]:
    """plan_windows within each dataset, because frame-rate sections and their warnings belong to one dataset."""
    config = job.config
    merged: dict[str, Any] = {"items": [], "total": 0, "source_rates": []}
    for dataset in datasets or [{"id": "", "name": ""}]:
        prefix = f'Dataset "{dataset["name"]}": ' if len(job.datasets) > 1 else ""
        plan = plan_windows(
            job.family,
            config.frames,
            [item for item in items if item["dataset_id"] == dataset["id"]],
            extraction=config.frame_extraction,
            stride=config.frame_stride,
            sample=config.frame_sample,
        )
        for level, messages in [("errors", errors), ("warnings", warnings)]:
            for item_id, message in plan[level]:
                if item_id:
                    messages.add(prefix + message, dataset_id=dataset["id"], item_id=item_id, view="preview")
                else:
                    messages.append(prefix + message)
        merged["items"].extend(plan["items"])
        merged["total"] += plan["total"]
        merged["source_rates"] = sorted({*merged["source_rates"], *plan["source_rates"]})
    return merged


def _add_duplicate_warnings(
    datasets: list[dict[str, Any]], items: list[dict[str, Any]], warnings: ReviewMessages
) -> None:
    """Name files that train twice because two listed datasets hold the same content."""
    names = {dataset["id"]: dataset["name"] for dataset in datasets}
    by_size: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        try:
            by_size.setdefault(Path(item["absolute_path"]).stat().st_size, []).append(item)
        except OSError:
            # Review reports unreadable media through the dataset checks.
            continue
    content_hash = import_module("musubi_gui.datasets").content_hash
    by_content: dict[str, list[dict[str, Any]]] = {}
    # Only files whose size repeats in another dataset can match, so most are never read.
    for group in by_size.values():
        if len({item["dataset_id"] for item in group}) > 1:
            for item in group:
                by_content.setdefault(content_hash(Path(item["absolute_path"])), []).append(item)
    for group in by_content.values():
        if len({item["dataset_id"] for item in group}) < 2:
            continue
        summary = "Identical files in several datasets train more than once"
        copies = [
            {
                "label": f'{item["name"]} in "{names[item["dataset_id"]]}"',
                "dataset_id": item["dataset_id"],
                "item_id": item["id"],
            }
            for item in group
        ]
        warnings.add(f"{summary}: " + ", ".join(copy["label"] for copy in copies) + ".", summary=summary, items=copies)


def review_job(root: Path, job: Job, settings: Settings, *, fixture_mode: bool = False) -> dict[str, Any]:
    from musubi_gui.training_options import timestep_range_policy

    errors = ReviewMessages([])
    warnings = ReviewMessages(
        [
            "Wan has short-run execution evidence on the 96 GB host using the documented native and converted weights; other artifacts and larger workloads need qualification."
            if job.family == "wan"
            else "H3 has short-run execution evidence on the 96 GB host; training quality and larger workloads remain unqualified.",
            "Changes to geometry, duration or memory controls need their own capacity validation.",
        ]
    )
    if 0 < job.config.steps < LOW_STEPS_WARNING:
        warnings.append(
            f"Training steps: {job.config.steps} steps can check that training runs, but expect no visible change "
            f"in samples below about {LOW_STEPS_WARNING} steps."
        )
    if job.config.preview_every and job.config.recipe != PAIRED_RECIPE:
        warnings.append(
            "In-run previews pause training for inference and decoding, retain the optimizer and dataset position, and need separate GPU capacity qualification. Text encoding happens before the training model loads; VAEs are kept on CPU between previews."
        )
        if job.family == "wan":
            warnings.append(
                "Wan in-run previews use only the trained low-noise expert across all sampling timesteps; they are diagnostic previews and differ from the final two-expert sample."
            )
    if job.family == "wan" and job.config.recipe == PAIRED_RECIPE:
        if job.config.preview_every:
            warnings.append(
                "Paired previews end a training phase, release its GPU process, sample both experts, then reload optimizer state. Extra boundaries restart the shuffled dataset pass. Experts without updates use their base weights, labelled with zero trained steps."
            )
        warnings.append(
            "Alternating experts: native GPU qualification covers 256×256, 17 frames, rank 8 and N=1. Useful learning and production-size paired capacity remain unqualified."
        )
    if job.family == "h3" and job.config.width * job.config.height > 640 * 352:
        warnings.append(
            "Geometry: H3 is qualified up to 640×352 on the target PC; 832×480 ran out of VRAM at the first step."
        )
    settings = effective_settings(job, settings)
    if job.config.optimizer == "AdamW8bit":
        from musubi_gui.training_options import optimizer_options

        options = optimizer_options(settings, fixture_mode=fixture_mode)
        if "AdamW8bit" not in options["optimizers"]:
            errors.add(f"Optimizer: {options['reason']}", field="optimizer")
    for label, value in [("Output root", settings.output_root), ("Cache root", settings.cache_root)]:
        if not value:
            errors.append(f"{label}: configure a dedicated directory in Settings.")
    try:
        validate_paths(settings)
        if fixture_mode:
            marker = Path(settings.checkout) / ".musubi-gui-fixture.json"
            if json.loads(marker.read_text(encoding="utf-8")) != {"version": 1, "kind": "synthetic-worker"}:
                errors.append("Fixture marker is not the expected synthetic-worker format.")
            component_names = (
                ["wan_low", "wan_high", "wan_vae", "wan_t5"]
                if job.family == "wan"
                else ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]
            )
            for name in component_names:
                if not settings.models.get(name) or not Path(settings.models[name]).is_file():
                    errors.append(f"Fixture component missing: {name}.")
            warnings.insert(0, "SYNTHETIC FIXTURE: these subprocesses do not establish GPU/model support.")
        else:
            errors.extend(validate_wan(settings) if job.family == "wan" else validate_h3(settings))
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors.append(f"Model paths/format: {error}")
    model_provenance: dict[str, dict[str, str]] = {}
    for name, value in settings.models.items():
        if not name.startswith(job.family + "_") or not value.endswith(".safetensors"):
            continue
        try:
            file = within_root(Path(value), [Path(location) for location in settings.model_roots])
            metadata = safetensors_header(file).get("__metadata__", {})
            if isinstance(metadata, dict):
                model_provenance[name] = {
                    key: redact(item)
                    for key, item in metadata.items()
                    if isinstance(key, str) and isinstance(item, str)
                }
                precision = model_provenance[name].get("source_precision", "")
                if "lossy" in precision.lower():
                    warnings.append(f"{name}: {precision}")
        except (ValueError, OSError, TypeError):
            # Format/path diagnostics are already reported above; provenance never grants compatibility.
            continue
    actual_revision = ""
    try:
        actual_revision = upstream_revision(Path(settings.checkout))
        if actual_revision != UPSTREAM and not fixture_mode:
            errors.append(
                "Upstream revision differs from the pinned recipe. Validate that revision before preparation."
            )
    except (ValueError, OSError) as error:
        errors.append(f"Installation: {error}")
    if not settings.python or not Path(settings.python).is_file():
        errors.append("Training Python: install the supported Musubi version in Settings.")
    effective_items: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    audio_coverage: dict[str, Any] = {"included": 0, "supervised": 0, "missing": 0, "items": []}
    if job.datasets:
        store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
        for entry in job.datasets:
            try:
                dataset = store.get(entry.dataset_id)
            except KeyError:
                errors.add(
                    f'Dataset "{entry.dataset_id}": this dataset is no longer registered. Remove it from the job.',
                    field=f"remove-dataset-{entry.dataset_id}",
                )
                continue
            datasets.append(dataset)
            if entry.resolution is not None:
                multiple = 16 if job.family == "wan" else 32
                for dimension, dimension_value in zip(("width", "height"), entry.resolution, strict=True):
                    if dimension_value < 64 or dimension_value % multiple:
                        errors.add(
                            f'Dataset "{dataset["name"]}": resolution {dimension} must be at least 64 and a multiple of {multiple}.',
                            field=f"resolution-{entry.dataset_id}-{dimension}",
                        )
            if entry.repeats is not None and entry.repeats < 1:
                errors.add(
                    f'Dataset "{dataset["name"]}": enter repeats of at least one, or leave them blank to use the job\'s.',
                    field=f"dataset-{entry.dataset_id}",
                )
            prefix = f'Dataset "{dataset["name"]}": ' if len(job.datasets) > 1 else ""
            try:
                effective_items.extend(_review_items(store, job, dataset, prefix, errors, warnings, audio_coverage))
            except (ValueError, OSError, ImportError, HTTPException) as error:
                errors.add(f'Dataset "{dataset["name"]}": {error}', dataset_id=entry.dataset_id)
    # Image sections always cache one frame. Clip controls matter only to videos;
    # frames still controls generated samples/previews even when training uses images.
    errors.extend(
        configuration_errors(
            job, has_videos=not effective_items or any(item["kind"] == "video" for item in effective_items)
        )
    )
    output = Path(settings.output_root) / "runs" / f"{job_folder(job.name)}-{{timestamp}}"
    cache = Path(settings.cache_root) / "jobs" / job.id
    snapshot = output
    _add_duplicate_warnings(datasets, effective_items, warnings)
    if job.family == "h3" and any(item["kind"] == "image" for item in effective_items):
        warnings.extend(H3_IMAGE_WARNINGS)
    windows = _plan_job_windows(job, datasets, effective_items, errors, warnings)
    sections = plan_sections(job, effective_items, snapshot, cache)
    balance = section_balance(job, sections, windows["items"], {item["id"]: item["name"] for item in datasets})
    if len(balance) > 1:
        warnings.extend(
            f'Balance: "{row["dataset"]}" ({row["kind"]}s) has {row["share"]:.0%} of the training samples per epoch, '
            "so the other sections have little influence. Consider more repeats on the others."
            for row in balance
            if row["share"] > BALANCE_WARNING_SHARE
        )
    disk = disk_preflight(job, settings, effective_items, windows["items"], fixture_mode=fixture_mode)
    warnings.extend(volume["warning"] for volume in disk["volumes"] if volume["warning"])
    stages: list[Stage] = []
    try:
        stages = build_stages(job, settings, snapshot, cache, {item["kind"] for item in effective_items})
        if settings.output_root:
            within_root(output, [Path(settings.output_root)])
        if settings.cache_root:
            within_root(cache, [Path(settings.cache_root)])
    except (ValueError, KeyError) as error:
        errors.append(f"Commands: {error}")
    return {
        "valid": not errors,
        "optimizer": job.config.optimizer,
        "timestep_sampling": job.config.timestep_sampling,
        "timestep_range_policy": timestep_range_policy(job),
        "timestep_parameters": (
            {"sigmoid_scale": job.config.sigmoid_scale, "discrete_flow_shift": job.config.discrete_flow_shift}
            if job.family == "wan" and job.config.timestep_sampling == "shift"
            else {"sigmoid_scale": job.config.sigmoid_scale}
            if job.family == "wan" and job.config.timestep_sampling == "sigmoid"
            else {}
        ),
        "error_targets": errors.targets,
        "warning_targets": warnings.targets,
        "errors": errors,
        "warnings": warnings,
        "job_id": job.id,
        "revision": job.revision,
        "dataset": datasets[0] if datasets else {},
        "datasets": datasets,
        "effective_items": effective_items,
        "effective_count": len(effective_items),
        "audio_coverage": audio_coverage,
        "video_windows": windows,
        "cache_policy": {"latent_cache": job.config.latent_cache_policy, "text_cache": job.config.text_cache_policy},
        "disk": disk,
        "models": {key: value for key, value in settings.models.items() if key.startswith(job.family + "_")},
        "model_provenance": model_provenance,
        "expected_upstream": UPSTREAM,
        "upstream_revision": actual_revision,
        "paths": {"output": str(output), "cache": str(cache), "snapshot": str(snapshot)},
        "sample": {
            "width": sample_geometry(job.config)[0],
            "height": sample_geometry(job.config)[1],
            "frames": sample_geometry(job.config)[2],
            "fps": 16 if job.family == "wan" else 24,
            "duration": sample_geometry(job.config)[2] / (16 if job.family == "wan" else 24),
            "native_preview_frames": native_preview_frames(job.family, job.config),
        },
        "sections": balance,
        "dataset_toml": dataset_toml(job, sections),
        "stages": [stage.record() for stage in stages],
        "expert_schedule": (
            {
                "interval": job.config.expert_interval,
                "counts": expert_steps(job.config.steps, job.config.expert_interval),
                "phases": [
                    phase.record()
                    for phase in alternating_phases(
                        job.config.steps, job.config.expert_interval, preview_every=job.config.preview_every
                    )
                ],
            }
            if job.family == "wan"
            and job.config.recipe == PAIRED_RECIPE
            and job.config.expert_interval > 0
            and job.config.steps > 0
            and math.ceil(job.config.steps / job.config.expert_interval) <= MAX_PHASES
            and not any(message.startswith("Preview interval:") for message in errors)
            else None
        ),
    }

"""Versioned job drafts and deterministic plans for the pinned LoRA recipe."""

import builtins
import json
import math
import re
import threading
import uuid
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from musubi_gui.diagnostics import upstream_revision
from musubi_gui.h3 import h3_stages, validate_h3
from musubi_gui.reference import CAPTION, UPSTREAM, Stage, redact, validate_wan, wan_stages
from musubi_gui.settings import Settings, atomic_json, load_settings, validate_paths, within_root

_lock = threading.RLock()


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    recipe: str = "low-noise"
    components: dict[str, str] = Field(default_factory=dict)
    width: int = 256
    height: int = 256
    frames: int = 17
    batch_size: int = 1
    repeats: int = 1
    enable_bucket: bool = False
    bucket_no_upscale: bool = False
    rank: int = 8
    alpha: int = 8
    learning_rate: float = 0.0001
    steps: int = 2
    gradient_accumulation: int = 1
    blocks_to_swap: int = 36
    gradient_checkpointing: bool = True
    fp8_scaled: bool = True
    offload_inactive_dit: bool = False
    block_swap_h2d_only: bool = False
    audio_policy: Literal["require", "allow-missing"] = "require"
    save_every: int = 1
    save_state: bool = False
    sample_enabled: bool = True
    sample_prompt: str = CAPTION
    sample_steps: int = 4
    seed: int = 42


def default_config(family: str) -> JobConfig:
    if family == "h3":
        return JobConfig(
            recipe="guidance-loss",
            frames=124,
            rank=4,
            alpha=4,
            blocks_to_swap=48,
            fp8_scaled=False,
            block_swap_h2d_only=True,
            sample_prompt=CAPTION + " A steady sine tone plays.",
        )
    return JobConfig()


class JobDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Untitled job"
    family: Literal["wan", "h3"] = "wan"
    dataset_id: str = ""
    config: JobConfig = Field(default_factory=JobConfig)

    @model_validator(mode="before")
    @classmethod
    def family_defaults(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("family") == "h3":
            supplied = value.get("config", {})
            if isinstance(supplied, dict):
                return {**value, "config": {**default_config("h3").model_dump(), **supplied}}
        return value


class Job(JobDraft):
    version: Literal[1] = 1
    id: str
    revision: int


class JobUpdate(JobDraft):
    revision: int


class JobExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    format: Literal["musubi-gui-job"]
    draft: JobDraft


class NamedPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class Preset(NamedPreset):
    version: Literal[1] = 1
    id: str
    family: Literal["wan", "h3"]
    config: JobConfig


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

    return JobExport(version=1, format="musubi-gui-job", draft=JobDraft.model_validate(scrub(draft.model_dump())))


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
            "rank",
            "alpha",
            "learning_rate",
            "steps",
            "gradient_accumulation",
            "gradient_checkpointing",
            "save_every",
            "save_state",
            "sample_enabled",
            "sample_prompt",
            "sample_steps",
            "seed",
        }
    }
    for key in ["width", "height"]:
        value = getattr(job.config, key)
        if value >= 64 and value % (32 if family == "h3" else 16) == 0:
            shared[key] = value
    config = default_config(family).model_copy(update=shared)
    return JobDraft(name=job.name, family=family, dataset_id=job.dataset_id, config=config)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid job identity.")
    return value


class JobStore:
    def __init__(self, root: Path):
        self.root = root / "jobs"

    def get(self, job_id: str, revision: int | None = None) -> Job:
        directory = self.root / identifier(job_id)
        files = list(directory.glob("*.json"))
        if not files:
            raise FileNotFoundError("Job not found.")
        if any(not file.stem.isdigit() for file in files):
            raise ValueError("Unrecognised job storage; preserve the files before repair.")
        number = revision if revision is not None else max(int(file.stem) for file in files)
        job = Job.model_validate_json((directory / f"{number}.json").read_text(encoding="utf-8"))
        if job.id != job_id or job.revision != number:
            raise ValueError("Stored job identity/revision does not match its location; preserve it before repair.")
        return job

    def list(self) -> list[Job]:
        return [self.get(directory.name) for directory in sorted(self.root.glob("*")) if directory.is_dir()]

    def create(self, draft: JobDraft) -> Job:
        with _lock:
            job = Job(**draft.model_dump(), id=uuid.uuid4().hex, revision=1)
            directory = self.root / job.id
            directory.mkdir(parents=True)
            atomic_json(directory / "1.json", job.model_dump())
            return job

    def save(self, job_id: str, draft: JobDraft, expected_revision: int) -> Job:
        with _lock:
            old = self.get(job_id)
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
        with _lock:
            old = self.get(job_id)
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
        return [
            Preset.model_validate_json(file.read_text(encoding="utf-8"))
            for file in sorted((self.root.parent / "presets").glob("*.json"))
        ]

    def create_preset(self, job_id: str, name: str) -> Preset:
        job = self.get(job_id)
        preset = Preset(
            name=name, id=uuid.uuid4().hex, family=job.family, config=export_draft(as_draft(job)).draft.config
        )
        directory = self.root.parent / "presets"
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / f"{preset.id}.json", preset.model_dump())
        return preset

    def apply_preset(self, job_id: str, selection: PresetSelection) -> Job:
        with _lock:
            job = self.get(job_id)
            identifier(selection.preset_id)
            preset = Preset.model_validate_json(
                (self.root.parent / "presets" / f"{selection.preset_id}.json").read_text(encoding="utf-8")
            )
            if preset.id != selection.preset_id:
                raise ValueError("Preset identity mismatch; preserve its file before repair.")
            if job.family != preset.family:
                raise ValueError("This preset belongs to another model. Preview and accept a model conversion first.")
            draft = as_draft(job).model_copy(update={"config": preset.config})
            return self.save(job_id, draft, selection.revision)


def load_job(root: Path, job_id: str) -> Job:
    return JobStore(root).get(job_id)


def list_jobs(root: Path) -> list[Job]:
    return JobStore(root).list()


def effective_settings(job: Job, settings: Settings) -> Settings:
    return settings.model_copy(update={"models": {**settings.models, **job.config.components}})


def configuration_errors(job: Job) -> list[str]:
    config = job.config
    errors: list[str] = []
    if not job.name.strip():
        errors.append("Name: enter a job name.")
    if not job.dataset_id:
        errors.append("Dataset: select a registered dataset.")
    if job.family == "wan":
        if config.recipe != "low-noise":
            errors.append(
                "Recipe: only low-noise Wan training is currently available; high-noise/combined are unqualified."
            )
        if config.width < 64 or config.height < 64 or config.width % 16 or config.height % 16:
            errors.append("Geometry: Wan width and height must be at least 64 and multiples of 16.")
        if config.frames < 1 or (config.frames - 1) % 4:
            errors.append("Frames: Wan requires 4*n+1 frames.")
        if not 0 <= config.blocks_to_swap <= 39:
            errors.append("Block swapping: select between 0 and 39 Wan blocks.")
        if not config.fp8_scaled:
            errors.append("Memory: runtime FP8 scaling from native FP16 weights is required by this Wan recipe.")
        if config.block_swap_h2d_only:
            errors.append("Memory: H2D-only swapping belongs to the H3 recipe, not the initial Wan recipe.")
        allowed_components = {"wan_low", "wan_high", "wan_vae", "wan_t5"}
    else:
        if config.recipe != "guidance-loss":
            errors.append(
                "Recipe: initial H3 T2VA supports guidance loss only; other recipes need separate validation."
            )
        if config.width < 64 or config.height < 64 or config.width % 32 or config.height % 32:
            errors.append("Geometry: H3 width and height must be at least 64 and multiples of 32.")
        if not 124 <= config.frames <= 345 or (config.frames - 5) % 17:
            errors.append("Frames: H3 requires 17*n+5 frames in the released 124–345 range.")
        if not 0 <= config.blocks_to_swap <= 53:
            errors.append("Block swapping: select between 0 and 53 H3 blocks.")
        if config.fp8_scaled:
            errors.append("Memory: H3 does not support FP8 transformer weights/runtime scaling.")
        if not config.block_swap_h2d_only:
            errors.append("Memory: H3 guidance loss requires H2D-only swapping for the validated target-PC recipe.")
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
    if config.sample_enabled and not config.sample_prompt.strip():
        errors.append("Sampling: enter a prompt or disable the final sample.")
    if any(key not in allowed_components for key in config.components):
        errors.append("Components: only the four supported component keys for the selected model are accepted.")
    return errors


def dataset_toml(job: Job, directory: Path, cache: Path, *, kinds: set[str] | None = None) -> str:
    """Directory is an immutable managed snapshot, never the mutable source dataset."""
    config = job.config

    def quote(path: Path) -> str:
        return json.dumps(path.as_posix(), ensure_ascii=False)

    general = (
        f"[general]\nresolution = [{config.width}, {config.height}]\nbatch_size = {config.batch_size}\n"
        f"num_repeats = {config.repeats}\nenable_bucket = {str(config.enable_bucket).lower()}\n"
        f"bucket_no_upscale = {str(config.bucket_no_upscale).lower()}\n\n"
    )
    if kinds is None:
        files = list(directory.rglob("*")) if directory.exists() else []
        images = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
        videos = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
        kinds = {"image" for file in files if file.suffix.lower() in images}
        kinds.update("video" for file in files if file.suffix.lower() in videos)
        if not kinds:
            kinds = {"video"}
    sections = []
    for kind in ["image", "video"]:
        if kind not in kinds:
            continue
        cache_directory = cache / ("dataset-images" if kind == "image" else "dataset")
        sections.append(
            f"[[datasets]]\n{kind}_directory = {quote(directory)}\ncache_directory = {quote(cache_directory)}\n"
            'caption_extension = ".txt"\n'
            + (f'target_frames = [{config.frames}]\nframe_extraction = "head"\n' if kind == "video" else "")
        )
    return general + "\n".join(sections)


def _set_argument(args: list[str], flag: str, value: str) -> None:
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args.extend([flag, value])


def build_stages(job: Job, settings: Settings, run: Path, cache: Path) -> list[Stage]:
    settings = effective_settings(job, settings)
    config = job.config
    recipe_stages = wan_stages if job.family == "wan" else h3_stages
    stages = [stage for stage in recipe_stages(settings, run, cache) if stage.name != "fixture"]
    for stage in stages:
        args = stage.args
        if stage.name == "train":
            for flag, value in [
                ("--network_dim", config.rank),
                ("--network_alpha", config.alpha),
                ("--learning_rate", config.learning_rate),
                ("--max_train_steps", config.steps),
                ("--gradient_accumulation_steps", config.gradient_accumulation),
                ("--blocks_to_swap", config.blocks_to_swap),
                ("--save_every_n_steps", config.save_every),
                ("--seed", config.seed),
                ("--output_name", f"{job.family}-{job.id}"),
            ]:
                _set_argument(args, flag, str(value))
            if config.save_state:
                args.extend(["--save_state", "--save_state_on_train_end"])
        if stage.name == "generate":
            _set_argument(args, "--prompt", config.sample_prompt)
            _set_argument(args, "--infer_steps", str(config.sample_steps))
            _set_argument(args, "--seed", str(config.seed))
            _set_argument(args, "--blocks_to_swap", str(config.blocks_to_swap))
            _set_argument(args, "--video_length", str(config.frames))
            index = args.index("--video_size")
            args[index + 1 : index + 3] = [str(config.height), str(config.width)]
            _set_argument(args, "--lora_weight", str(run / "checkpoints" / f"{job.family}-{job.id}.safetensors"))
    return [stage for stage in stages if stage.name != "generate" or config.sample_enabled]


def review_job(root: Path, job: Job, settings: Settings, *, fixture_mode: bool = False) -> dict[str, Any]:
    errors = configuration_errors(job)
    warnings = [
        "Wan support remains unresolved until the reference scenario passes on the specified 96 GB host."
        if job.family == "wan"
        else "H3 has short-run execution evidence on the 96 GB host; training quality and larger workloads remain unqualified.",
        "Changes to geometry, duration or memory controls need their own capacity validation.",
    ]
    settings = effective_settings(job, settings)
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
        errors.append("Training Python: select an existing executable in Settings.")
    effective_items: list[dict[str, Any]] = []
    dataset: dict[str, Any] = {}
    audio_coverage: dict[str, Any] = {"included": 0, "supervised": 0, "missing": 0, "items": []}
    if job.dataset_id:
        try:
            store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
            dataset = store.get(job.dataset_id)
            effective_items = [item for item in store.items(job.dataset_id) if not item["excluded"] and item["valid"]]
            if not effective_items:
                errors.append("Dataset: no valid, included media remain.")
            if job.family == "h3" and any(item["kind"] != "video" for item in effective_items):
                errors.append(
                    "Dataset: this initial video recipe requires videos; exclude images or select another dataset."
                )
            for item in effective_items:
                if not item["caption"]["exists"]:
                    warnings.append(f"{item['name']}: no caption sidecar; the frozen caption will be empty.")
                frames = item.get("metadata", {}).get("frames")
                if item["kind"] == "video" and isinstance(frames, int) and frames < job.config.frames:
                    errors.append(
                        f"{item['name']}: {frames} decoded frames cannot supply the requested {job.config.frames}; shorten the job or choose a longer video."
                    )
                if job.family == "h3" and item["kind"] == "video":
                    inspection: dict[str, Any] = store.inspect_h3(job.dataset_id, item["id"])
                    audio_coverage["included"] += 1
                    audio_coverage["items"].append({"id": item["id"], "name": item["name"], **inspection})
                    errors.extend(f"{item['name']}: {message}" for message in inspection.get("errors", []))
                    warnings.extend(f"{item['name']}: {message}" for message in inspection.get("warnings", []))
                    if inspection.get("source") == "missing":
                        audio_coverage["missing"] += 1
                        if job.config.audio_policy == "require":
                            errors.append(
                                f"{item['name']}: audio policy requires supervised audio; supply valid aligned audio or allow missing audio explicitly."
                            )
                        else:
                            warnings.append(
                                f"{item['name']}: absent audio is allowed and excluded from audio supervision; no audio is synthesized."
                            )
                    elif not inspection.get("errors"):
                        audio_coverage["supervised"] += 1
        except (ValueError, OSError, ImportError, HTTPException) as error:
            errors.append(f"Dataset: {error}")
    output = Path(settings.output_root) / "jobs" / job.id
    cache = Path(settings.cache_root) / "jobs" / job.id
    snapshot = output / f"revision-{job.revision}"
    stages: list[Stage] = []
    try:
        stages = build_stages(job, settings, snapshot, cache)
        if settings.output_root:
            within_root(output, [Path(settings.output_root)])
        if settings.cache_root:
            within_root(cache, [Path(settings.cache_root)])
    except (ValueError, KeyError) as error:
        errors.append(f"Commands: {error}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "job_id": job.id,
        "revision": job.revision,
        "dataset": dataset,
        "effective_items": effective_items,
        "effective_count": len(effective_items),
        "audio_coverage": audio_coverage,
        "models": {key: value for key, value in settings.models.items() if key.startswith(job.family + "_")},
        "expected_upstream": UPSTREAM,
        "upstream_revision": actual_revision,
        "paths": {"output": str(output), "cache": str(cache), "snapshot": str(snapshot)},
        "dataset_toml": dataset_toml(
            job, snapshot / "media", cache, kinds={item["kind"] for item in effective_items} or None
        ),
        "stages": [{"name": stage.name, "args": stage.args} for stage in stages],
    }


@contextmanager
def job_errors() -> Iterator[None]:
    try:
        yield
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
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

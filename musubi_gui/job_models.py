"""Versioned Job drafts, their saved and exported shapes, presets and model conversion."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from musubi_gui.recipe import CAPTION
from musubi_gui.redaction import redact
from musubi_gui.wan_pair import PAIRED_RECIPE

NEW_DRAFT_STEPS = 2000


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    recipe: str = "low-noise"
    expert_interval: int = 30
    wan_steps_per_expert: bool = True
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
    sample_memory_policy: Literal["automatic", "conservative"] = "automatic"
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

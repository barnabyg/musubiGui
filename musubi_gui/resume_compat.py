"""Which edits in a newer Job revision a resumed segment can take, compared field by field with the frozen revision.

A saved optimizer state belongs to one LoRA shape, dataset and training recipe. Fields that only change what the
segment saves, samples or how it fits in memory can change on resume; anything else needs a new Run. A field this
module doesn't name, including one added to the Job later, needs a new Run until someone decides otherwise.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from musubi_gui.job_models import Job

Tier = Literal["editable", "warned", "new_run"]

# Applied by the resumed segment as the new revision has them.
EDITABLE: dict[str, str] = {
    "name": "Name",
    "steps": "Training steps",
    "sample_enabled": "Generate a final sample",
    "sample_prompt": "Sample prompt",
    "sample_steps": "Sample inference steps",
    "sample_width": "Sample width",
    "sample_height": "Sample height",
    "sample_frames": "Sample frames",
    "sample_memory_policy": "Generation memory policy",
    "preview_every": "Preview every N steps",
    "save_every": "Save every N steps",
    "keep_checkpoints": "Keep checkpoints",
    "blocks_to_swap": "Blocks to swap",
    "gradient_checkpointing": "Gradient checkpointing",
    "offload_inactive_dit": "Offload inactive DiT",
}
# Applied on resume, with a warning that changing them mid-training changes what the LoRA learns. Empty until the
# trainer wrapper overrides the restored optimizer's learning rate; learning_rate, timestep_sampling, sigmoid_scale,
# discrete_flow_shift and gradient_accumulation then move here from NEW_RUN.
WARNED: dict[str, str] = {}
# Labels for the fields known to need a new Run; any other field needs one too, labelled by its name.
NEW_RUN: dict[str, str] = {
    "family": "Model family",
    "datasets": "Datasets",
    "recipe": "Recipe",
    "components": "Model components",
    "rank": "LoRA rank",
    "alpha": "LoRA alpha",
    "optimizer": "Optimizer",
    "learning_rate": "Learning rate",
    "timestep_sampling": "Timestep strategy",
    "sigmoid_scale": "Sigmoid scale",
    "discrete_flow_shift": "Flow shift",
    "gradient_accumulation": "Gradient accumulation",
    "fp8_scaled": "Runtime FP8 scaling",
    "block_swap_h2d_only": "H2D-only block swapping",
    "save_state": "Save optimizer/training state",
    "expert_interval": "Expert interval",
    "wan_steps_per_expert": "Steps count per expert",
    "repeats": "Repeats",
    "width": "Default training width",
    "height": "Default training height",
    "frames": "Frames",
    "frame_extraction": "Frame extraction",
    "frame_stride": "Slide stride",
    "frame_sample": "Windows per clip",
    "enable_bucket": "Enable bucketing",
    "bucket_no_upscale": "Do not upscale buckets",
    "batch_size": "Dataset batch size",
    "latent_cache_policy": "Latent cache policy",
    "text_cache_policy": "Text cache policy",
    "audio_policy": "Audio policy",
    "seed": "Seed",
}
DATA_ORDER_NOTE = (
    "Resuming restores the saved optimizer, scheduler and RNG state, but the data order restarts from the "
    "beginning of the dataset."
)
# Identity of a revision rather than its content.
_IGNORED = frozenset({"id", "version", "revision", "config"})


class ResumeIncompatible(ValueError):
    """A revision that can't resume a saved state; its message names the fields and the steps problem."""


@dataclass(frozen=True)
class Change:
    field: str
    label: str
    tier: Tier
    before: Any
    after: Any

    def record(self) -> dict[str, Any]:
        return {"field": self.field, "label": self.label, "tier": self.tier, "before": self.before, "after": self.after}


def tier(field: str, before: Any, after: Any) -> Tier:
    if field == "save_state":
        # Turning state saving on is safe: Save and stop saved this state even when the Job kept none.
        return "editable" if after else "new_run"
    if field in EDITABLE:
        return "editable"
    if field in WARNED:
        return "warned"
    return "new_run"


def label(field: str) -> str:
    return EDITABLE.get(field) or WARNED.get(field) or NEW_RUN.get(field) or field


def flatten(job: dict[str, Any]) -> dict[str, Any]:
    """A Job revision's comparable fields in one mapping: its config fields beside name, family and datasets.

    Read through the Job model, so a field recorded before it existed compares by the default it now means.
    """
    try:
        normal = Job.model_validate(job).model_dump()
    except ValidationError as error:
        raise ValueError(
            "This Run's frozen Job can't be read by this version, so changes can't be checked; prepare a new Run."
        ) from error
    return {**{key: value for key, value in normal.items() if key not in _IGNORED}, **normal["config"]}


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """The fields whose values differ between two flattened revisions, each with its resume tier."""
    return [
        Change(
            field, label(field), tier(field, before.get(field), after.get(field)), before.get(field), after.get(field)
        )
        for field in sorted(before.keys() | after.keys())
        if before.get(field) != after.get(field)
    ]


def compatibility(
    frozen: dict[str, Any], revision: dict[str, Any], *, saved_step: int | None, target_steps: int
) -> dict[str, Any]:
    """Whether a segment resuming a state saved at `saved_step` can train `revision` until `target_steps`.

    `frozen` is the revision the saved state was trained with. An unknown `saved_step` skips the total check.
    """
    changes = changed_fields(flatten(frozen), flatten(revision))
    errors = []
    if saved_step is not None and target_steps <= saved_step:
        errors.append(f"Training steps: choose a total after the saved step {saved_step}.")
    blocking = [change.label for change in changes if change.tier == "new_run"]
    return {
        "compatible": not blocking and not errors,
        "frozen_revision": frozen.get("revision"),
        "revision": revision.get("revision"),
        "saved_step": saved_step,
        "target_steps": target_steps,
        "changes": [change.record() for change in changes],
        "blocking": blocking,
        "errors": errors,
        "notes": [DATA_ORDER_NOTE],
    }


def refusal(result: dict[str, Any]) -> str:
    """Why `result` can't resume, naming the fields."""
    parts = []
    if result["blocking"]:
        parts.append(
            f"Revision {result['revision']} changes {', '.join(result['blocking'])}, which needs a new Run: "
            "the saved state was trained with the earlier values."
        )
    parts.extend(result["errors"])
    return " ".join(parts)


# Runs that ended, so their saved states can be inspected and resumed.
ENDED = frozenset({"completed", "stopped", "failed", "cancelled", "interrupted"})


def _saved_step(run: dict[str, Any]) -> tuple[bool, int | None]:
    """Whether `run` may have a resumable state, and its latest saved step when inspection has recorded one.

    Never hashes or probes: Job review reads only what Inspect saved states recorded, or that state folders exist.
    """
    recorded = run.get("saved_states") or []
    valid = [state for state in recorded if state.get("valid") and "resume_step" in state]
    if valid:
        return True, max(int(state["resume_step"]) for state in valid)
    if recorded:
        return False, None
    try:
        folders = (Path(run["directory"]) / "checkpoints").glob("*-state")
        return any(folder.is_dir() and not folder.name.startswith(".") for folder in folders), None
    except OSError:
        return False, None


def resume_review(runs: list[dict[str, Any]], job: dict[str, Any]) -> dict[str, Any] | None:
    """Job review's Resume compatibility: `job` against the Job's latest resumable training, or None without one.

    That is the most recently created ended Run or segment of the Job with a saved state.
    """
    candidates = []
    for run in runs:
        if run.get("job", {}).get("id") != job["id"] or run["status"] not in ENDED or run.get("processes"):
            continue
        resumable, step = _saved_step(run)
        if resumable:
            candidates.append((run, step))
    if not candidates:
        return None
    run, step = max(candidates, key=lambda candidate: candidate[0]["created"])
    source = {"run_id": run["id"], "run_status": run["status"]}
    try:
        return {**source, **compatibility(run["job"], job, saved_step=step, target_steps=job["config"]["steps"])}
    except ValueError as error:
        return {
            **source,
            "compatible": False,
            "frozen_revision": run["job"].get("revision"),
            "revision": job["revision"],
            "saved_step": step,
            "target_steps": job["config"]["steps"],
            "changes": [],
            "blocking": [],
            "errors": [str(error)],
            "notes": [],
        }

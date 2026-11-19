"""Which edits in a newer Job revision a resumed segment can take, compared field by field with the frozen revision.

A saved optimizer state belongs to one LoRA shape and training recipe. Fields that only change what the segment saves,
samples or how it fits in memory can change on resume. The learning rate, timestep distribution and gradient
accumulation can change too, with a warning, because they change what the LoRA learns from then on. The datasets and
their repeats, captions and media can change with a re-prepare: the segment takes a new snapshot of the datasets and
caches again only the items whose inputs changed. Anything else needs a new Run. A field this module doesn't name,
including one added to the Job later, needs a new Run until someone decides otherwise.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from musubi_gui.job_models import Job
from musubi_gui.run_chains import Chain, chains, step_range
from musubi_gui.run_paths import RunPaths

Tier = Literal["editable", "warned", "reprepare", "new_run"]

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
# Applied on resume, with a warning that changing them mid-training changes what the LoRA learns. The segment's
# arguments carry them; the trainer entry point writes the learning rate over the restored optimizer's.
WARNED: dict[str, str] = {
    "learning_rate": "Learning rate",
    "timestep_sampling": "Timestep strategy",
    "sigmoid_scale": "Sigmoid scale",
    "discrete_flow_shift": "Flow shift",
    "gradient_accumulation": "Gradient accumulation",
}
# Applied by a segment that snapshots the datasets again and rebuilds only the caches whose inputs changed. Caption
# and media edits in the datasets themselves resume this way too, though they change no field of the Job.
REPREPARE: dict[str, str] = {
    "datasets": "Datasets",
    "repeats": "Repeats",
}
# Labels for the fields known to need a new Run; any other field needs one too, labelled by its name.
NEW_RUN: dict[str, str] = {
    "family": "Model family",
    "recipe": "Recipe",
    "components": "Model components",
    "rank": "LoRA rank",
    "alpha": "LoRA alpha",
    "optimizer": "Optimizer",
    "fp8_scaled": "Runtime FP8 scaling",
    "block_swap_h2d_only": "H2D-only block swapping",
    "save_state": "Save optimizer/training state",
    "expert_interval": "Expert interval",
    "wan_steps_per_expert": "Steps count per expert",
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
WARNING = (
    "Changing these mid-training changes what the LoRA learns: the saved state was trained with the earlier values, "
    "and the result can differ from a Run trained with either throughout."
)
ACCUMULATION_WARNING = (
    "Gradient accumulation also changes what a step means: each step after the saved one averages a different "
    "number of batches, though steps still count optimizer updates."
)
DATA_ORDER_NOTE = (
    "Resuming restores the saved optimizer, scheduler and RNG state, but the data order restarts from the "
    "beginning of the dataset."
)
REPREPARE_NOTE = (
    "Resuming takes a new snapshot of the datasets and caches again only the items whose media or caption changed, "
    "before training continues from the saved step."
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


# Fields whose tier depends on the value they change to, with the values they can take.
_BY_VALUE: dict[str, tuple[Any, ...]] = {"save_state": (False, True)}


def tier(field: str, before: Any, after: Any) -> Tier:
    if field == "save_state":
        # Turning state saving on is safe: Save and stop saved this state even when the Job kept none.
        return "editable" if after else "new_run"
    if field in EDITABLE:
        return "editable"
    if field in WARNED:
        return "warned"
    if field in REPREPARE:
        return "reprepare"
    return "new_run"


def label(field: str) -> str:
    return EDITABLE.get(field) or WARNED.get(field) or REPREPARE.get(field) or NEW_RUN.get(field) or field


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
    frozen: dict[str, Any],
    revision: dict[str, Any],
    *,
    saved_step: int | None,
    target_steps: int,
    dataset: dict[str, Any] | None = None,
    dataset_error: str | None = None,
    preparation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Whether a segment resuming a state saved at `saved_step` can train `revision` until `target_steps`.

    `frozen` is the revision the saved state was trained with. An unknown `saved_step` skips the total check.
    `dataset` is how the datasets' current contents differ from the frozen snapshot, or `dataset_error` why they
    couldn't be read; `preparation` is the work a re-prepare would do, reported as given.
    """
    changes = changed_fields(flatten(frozen), flatten(revision))
    errors = []
    if saved_step is not None and target_steps <= saved_step:
        errors.append(f"Training steps: choose a total after the saved step {saved_step}.")
    blocking = [change.label for change in changes if change.tier == "new_run"]
    warned = {change.field for change in changes if change.tier == "warned"}
    warnings = [WARNING] if warned else []
    if "gradient_accumulation" in warned:
        warnings.append(ACCUMULATION_WARNING)
    fields_reprepare = any(change.tier == "reprepare" for change in changes)
    notes = [DATA_ORDER_NOTE]
    if dataset_error is not None:
        if fields_reprepare:
            errors.append(f"Datasets: {dataset_error}")
        else:
            notes.append(
                f"The datasets' current contents couldn't be read, so resuming keeps the frozen snapshot: {dataset_error}"
            )
    reprepare = dataset_error is None and (fields_reprepare or bool(dataset and dataset["changed"]))
    if reprepare:
        notes.append(REPREPARE_NOTE)
    return {
        "compatible": not blocking and not errors,
        "frozen_revision": frozen.get("revision"),
        "revision": revision.get("revision"),
        "saved_step": saved_step,
        "target_steps": target_steps,
        "changes": [change.record() for change in changes],
        "blocking": blocking,
        "errors": errors,
        # Shown with the warned fields; resuming asks the user to acknowledge them.
        "warnings": warnings,
        "notes": notes,
        # Whether resuming snapshots the datasets again, what changed in them and the preparation that follows.
        "reprepare": reprepare,
        "dataset_changes": dataset,
        "preparation": preparation if reprepare else None,
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
    return bool(_state_folders(run)), None


def _state_folders(run: dict[str, Any]) -> list[Path]:
    try:
        folders = (RunPaths.from_record(run).state).glob("*-state")
        return [folder for folder in folders if folder.is_dir() and not folder.name.startswith(".")]
    except OSError:
        return []


# A save at a step, named for the overall step since continuous numbering: `<name>-step00000300-state`.
_STEP_STATE = re.compile(r"-step(\d+)-state$")


def named_save_step(run: dict[str, Any]) -> int | None:
    """The step of `run`'s latest saved state, as inspection recorded it or, uninspected, as its folders are named.

    A folder name is not proof the state is complete; resuming inspects it. A completed Run's final `<name>-state` is
    its target step. Paired Wan states and segments restored before continuous numbering name local steps, so they
    report only what inspection recorded.
    """
    resumable, step = _saved_step(run)
    if step is not None or not resumable:
        return step
    config = run.get("job", {}).get("config") or {}
    steps = step_range(run)
    if config.get("recipe") == "alternating" or steps.legacy:
        return None
    named = []
    for folder in _state_folders(run):
        if match := _STEP_STATE.search(folder.name):
            named.append(int(match.group(1)))
        elif run["status"] == "completed":
            named.append(steps.target)
    return max(named, default=None)


def current_training(runs: list[dict[str, Any]], job_id: str) -> Chain | None:
    """The Job's current training: the chain of its Runs whose latest segment was created most recently.

    Resumed segments keep their Job, so a chain never mixes Jobs.
    """
    own = [run for run in runs if run.get("job", {}).get("id") == job_id and run["status"] != "deleting"]
    found = chains(own)
    return found[0] if found else None


def resume_source(runs: list[dict[str, Any]], job_id: str) -> tuple[dict[str, Any], int | None] | None:
    """The Run that resuming the Job's current training continues, with its inspected saved step, or None.

    Only an ended training resumes. Its source is the most recently created ended segment on its line with a saved
    state, so a latest segment that failed before saving resumes from the state it started from.
    """
    chain = current_training(runs, job_id)
    if chain is None or chain.latest["status"] not in ENDED or chain.latest.get("processes"):
        return None
    by_id = {run["id"]: run for run in chain.segments}
    candidates = []
    for run_id in chain.line:
        run = by_id[run_id]
        if run["status"] not in ENDED or run.get("processes"):
            continue
        resumable, step = _saved_step(run)
        if resumable:
            candidates.append((run, step))
    return max(candidates, key=lambda candidate: candidate[0]["created"], default=None)


def job_training(runs: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    """The Job page's view of its current training: the latest segment, and what Start would resume.

    Cheap enough to read on every change of the training: it never hashes or probes saved states.
    """
    chain = current_training(runs, job_id)
    source = resume_source(runs, job_id)
    resume = None
    if source is not None:
        run, _ = source
        resume = {"run_id": run["id"], "run_status": run["status"], "saved_step": named_save_step(run)}
    return {
        "run_id": chain.latest["id"] if chain else None,
        "chain_id": chain.id if chain else None,
        "status": chain.latest["status"] if chain else None,
        "resume": resume,
        **resumable(source[0]["job"] if source else None),
    }


def resumable(frozen_job: dict[str, Any] | None) -> dict[str, Any]:
    """The fields a segment resuming `frozen_job`'s training takes at any value, and those it takes only at some.

    Editing any other field, or one of the latter to another value, needs a new Run.
    """
    try:
        frozen = flatten(frozen_job) if frozen_job else {}
    except ValueError:
        frozen = {}
    fields = {*EDITABLE, *WARNED, *REPREPARE}
    values: dict[str, list[Any]] = {}
    for field, options in _BY_VALUE.items():
        taken = [
            value
            for value in options
            if value == frozen.get(field) or tier(field, frozen.get(field), value) != "new_run"
        ]
        if len(taken) == len(options):
            fields.add(field)
        else:
            values[field] = taken
    return {"resumable_fields": sorted(fields), "resumable_values": values}


def resume_review(
    runs: list[dict[str, Any]],
    job: dict[str, Any],
    *,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Job review's Resume compatibility: `job` against the Job's current training, or None if it can't resume.

    The compared Run is `resume_source`'s, which Start on the Job page resumes. `refresh(run)` compares the
    datasets' current contents with that Run's snapshot, returning `compatibility`'s dataset arguments.
    """
    found = resume_source(runs, job["id"])
    if found is None:
        return None
    run, step = found
    source = {"run_id": run["id"], "run_status": run["status"]}
    try:
        contents = refresh(run) if refresh else {}
        target = job["config"]["steps"]
        return {**source, **compatibility(run["job"], job, saved_step=step, target_steps=target, **contents)}
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
            "warnings": [],
            "notes": [],
            "reprepare": False,
            "dataset_changes": None,
            "preparation": None,
        }

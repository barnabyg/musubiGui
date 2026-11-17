"""Restoring a single saved optimizer state as a new Run segment, with the Run's own or a newer Job revision."""

import copy
import shutil
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from musubi_gui.recipe import Stage
from musubi_gui.run_estimate import freeze_training_timing
from musubi_gui.run_paths import create_run_directory
from musubi_gui.run_store import RunStore, utc_now
from musubi_gui.settings import Settings, atomic_json
from musubi_gui.snapshots import SNAPSHOT_ERROR, digest_file

if TYPE_CHECKING:
    from musubi_gui.job_models import Job
    from musubi_gui.resume_refresh import Refresh

# Outcome and process fields of the original Run that a restored segment, single or paired, starts without.
ENDED_FIELDS = (
    "stop_requested",
    "stop_kind",
    "finished",
    "execution_end_unknown",
    "exit_code",
    "checkpoints",
    "removed_duplicates",
    "retention",
    "sample_pair",
    "samples",
    "controller",
    "job_objects",
    "launch_pending",
    "control_request",
    "controls",
)


class SegmentFolder:
    """A new segment's Run folder, and the cache folder a refreshed snapshot adds, removed if building it fails."""

    def __init__(self, original: dict[str, Any], revision: "Job | None") -> None:
        name = revision.name if revision else original["job"]["name"]
        kind = "resume" if revision else "restore"
        self.path = create_run_directory(Path(original["directory"]).parent, name, kind)
        self.cache: Path | None = None

    def __enter__(self) -> "SegmentFolder":
        return self

    def __exit__(self, kind: type[BaseException] | None, *details: object) -> None:
        if kind is not None:
            discard_segment(self.path, self.cache)


def discard_segment(directory: Path, cache: Path | None) -> None:
    """Remove the folders of a segment that was never stored; no Run refers to them.

    A refused dataset snapshot's folder stays with its error to inspect, as a failed preparation's does.
    """
    if cache:
        # Preparation hasn't cached into it yet.
        with suppress(OSError):
            cache.rmdir()
    if not (directory / SNAPSHOT_ERROR).is_file():
        shutil.rmtree(directory, ignore_errors=True)


def resume_step(run: dict[str, Any], state: dict[str, Any]) -> int:
    """The global step a valid saved state continues from; a restored segment trains until a later step."""
    if state.get("paired"):
        if any(stage.get("training", {}).get("per_expert") for stage in run["stages"]):
            # Both experts train to the same target, so it must pass the further-trained expert.
            return max(int(count) for count in state["counts"].values())
        return int(state["schedule_step"])
    return int(state["optimizer_step"])


def segment_steps(run: dict[str, Any], state: dict[str, Any], target_steps: int) -> int:
    """Updates a segment trains to reach `target_steps`, after validating the requested total."""
    saved = resume_step(run, state)
    if target_steps <= saved:
        raise ValueError(f"Train until step: choose a step after the saved step {saved}.")
    if target_steps - saved > 1_000_000:
        raise ValueError(f"Train until step: choose at most 1000000 steps after the saved step {saved}.")
    return target_steps - saved


def continuous_segment(saved: int, target_steps: int) -> dict[str, Any]:
    """Segment fields that continue the original Run's step numbering up to `target_steps`."""
    return {
        "target_steps": target_steps,
        "step_offset": saved,
        "additional_steps": target_steps - saved,
        # Upstream still counts from zero; names, previews and progress add the offset.
        "counter_resets": False,
        "data_position_restored": False,
    }


def revision_stages(
    original: dict[str, Any], revision: "Job", directory: Path, refresh: "Refresh | None" = None
) -> list[Stage]:
    """Training and sampling stages built from `revision` for a segment in `directory`.

    They read `original`'s frozen dataset configuration, and through it its media snapshot and caches, so the
    segment prepares nothing. With `refresh`, they read the segment's own snapshot and caches instead, after cache
    stages that skip the items whose cache files preparation copied. Alternating Wan stages are templates, before
    the schedule splits them into phases.
    """
    from musubi_gui.job_stages import stage_templates

    settings = Settings.model_validate(original["settings"])
    if refresh:
        built = stage_templates(revision, settings, directory, refresh.cache, refresh.kinds)
        for stage in built:
            if stage.name in {"latent_cache", "text_cache"}:
                stage.args.append("--skip_existing")
        return built
    kinds = {item["kind"] for item in original["manifest"]["items"]}
    stages = [
        stage
        for stage in stage_templates(revision, settings, directory, Path(original["cache"]), kinds)
        if stage.name in {"train", "generate"}
    ]
    frozen = next(stage["args"] for stage in original["stages"] if stage["name"] == "train")
    dataset = frozen[frozen.index("--dataset_config") + 1]
    for stage in stages:
        if "--dataset_config" in stage.args:
            stage.args[stage.args.index("--dataset_config") + 1] = dataset
    return stages


def revision_inputs(original: dict[str, Any], revision: "Job", directory: Path) -> dict[str, str]:
    """The sealed inputs of a segment training `revision`: `original`'s, with this revision's preview prompt."""
    from musubi_gui.preview import preview_prompt

    previews = Path(original["directory"]) / "previews.json"
    files = {path: value for path, value in original["input_files"].items() if Path(path) != previews}
    if revision.config.preview_every:
        prompts = directory / "previews.json"
        atomic_json(prompts, preview_prompt(revision))
        files[str(prompts)] = digest_file(prompts)
    return files


def restored_segment(
    original: dict[str, Any],
    state: dict[str, Any],
    target_steps: int,
    *,
    revision: "Job | None" = None,
    changes: list[dict[str, Any]] | None = None,
    refresh: "Callable[[Path], Refresh] | None" = None,
) -> dict[str, Any]:
    """A prepared Run that resumes `state` in a new folder and trains until global step `target_steps`.

    With `revision`, the segment trains and samples as that revision of the Job says, recording it as its own
    and `changes` from the original's revision; without it, the original's commands are reused. With `refresh`,
    which snapshots the datasets into the segment's folder, the segment prepares its own caches first.
    """
    saved = resume_step(original, state)
    additional_steps = segment_steps(original, state, target_steps)
    with SegmentFolder(original, revision) as folder:
        directory = folder.path
        snapshot = refresh(directory) if refresh and revision else None
        folder.cache = snapshot.cache if snapshot else None
        record = copy.deepcopy(original)
        record.update(
            id=uuid.uuid4().hex,
            directory=str(directory),
            status="prepared",
            phase="train",
            fingerprint=original["fingerprint"] + ":restore:" + uuid.uuid4().hex,
            created=utc_now(),
            execution_started=None,
            processes=[],
            error=None,
            resume_of=original["id"],
            saved_states=[],
            segment={**continuous_segment(saved, target_steps), "optimizer_step_before": state["optimizer_step"]},
            state_source={"path": state["path"], "files": state["files"]},
        )
        for key in ENDED_FIELDS:
            record.pop(key, None)
        if revision:
            record["job"] = revision.model_dump()
            record["segment"].update(source_revision=original["job"]["revision"], changes=changes or [])
            prepared = [stage for stage in record["stages"] if stage["name"] not in {"train", "generate"}]
            if snapshot:
                # Its own snapshot carries its inputs, including the preview prompt.
                record.update(snapshot.fields(), order=time.time_ns())
                record["segment"].update(dataset_changes=snapshot.dataset)
                prepared = []
            else:
                record["input_files"] = revision_inputs(original, revision, directory)
            built = [
                {**stage.record(), "status": "pending"}
                for stage in revision_stages(original, revision, directory, snapshot)
            ]
            record["stages"] = prepared + built
            for stage in built:
                if stage["name"] == "train":
                    stage["args"][stage["args"].index("--max_train_steps") + 1] = str(additional_steps)
                    stage["args"].extend(["--resume", state["path"]])
            return record
        for stage in record["stages"]:
            if stage["name"] not in {"train", "generate"}:
                continue
            stage.update(status="pending")
            stage.pop("started", None)
            stage.pop("finished", None)
            args = stage["args"]
            for flag in ["--output_dir"] if stage["name"] == "train" else ["--save_path", "--lora_weight"]:
                if flag in args:
                    index = args.index(flag) + 1
                    args[index] = args[index].replace(original["directory"], str(directory))
            if stage["name"] == "train":
                args[args.index("--max_train_steps") + 1] = str(additional_steps)
                args.extend(["--resume", state["path"]])
        return record


def create_segment(store: RunStore, record: dict[str, Any], resume_of: str) -> None:
    """Freeze the segment's training timing, store it and write its inputs beside it."""
    directory = Path(record["directory"])
    try:
        record["stages"] = [
            freeze_training_timing(stage, record["job"]["family"], directory) for stage in record["stages"]
        ]
        store.create(record)
    except BaseException:
        discard_segment(directory, Path(record["cache"]) if record.get("refresh") else None)
        raise
    atomic_json(
        directory / "inputs.json",
        {"job": record["job"], "manifest": record["manifest"], "resume_of": resume_of, "segment": record["segment"]},
    )

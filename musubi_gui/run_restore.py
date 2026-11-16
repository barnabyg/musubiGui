"""Restoring a single saved optimizer state as a new Run segment."""

import copy
import uuid
from pathlib import Path
from typing import Any

from musubi_gui.run_estimate import freeze_training_timing
from musubi_gui.run_paths import create_run_directory
from musubi_gui.run_store import RunStore, utc_now
from musubi_gui.settings import atomic_json

# Outcome fields of the original Run that a restored segment starts without.
_ENDED_FIELDS = [
    "stop_requested",
    "stop_kind",
    "finished",
    "execution_end_unknown",
    "exit_code",
    "checkpoints",
    "removed_duplicates",
    "retention",
    "control_request",
    "controls",
]


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


def restored_segment(original: dict[str, Any], state: dict[str, Any], target_steps: int) -> dict[str, Any]:
    """A prepared Run that resumes `state` in a new folder and trains until global step `target_steps`."""
    saved = resume_step(original, state)
    additional_steps = segment_steps(original, state, target_steps)
    directory = create_run_directory(Path(original["directory"]).parent, original["job"]["name"], "restore")
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
    for key in _ENDED_FIELDS:
        record.pop(key, None)
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
    record["stages"] = [freeze_training_timing(stage, record["job"]["family"], directory) for stage in record["stages"]]
    store.create(record)
    atomic_json(
        directory / "inputs.json",
        {"job": record["job"], "manifest": record["manifest"], "resume_of": resume_of, "segment": record["segment"]},
    )

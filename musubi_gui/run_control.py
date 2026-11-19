"""Save now, Sample now and Save and stop requests for a training Run, and what became of them.

One request waits at a time, recorded as the Run's `control_request` and written to its request file. The trainer
reports how it handled it in the handled file; the backend moves the outcome to the Run's `controls` history.
A request still waiting when training reaches a sampling stage or the Run ends is dropped, so it can never fire in a
later stage or segment. One made after training's final step leaves the Run to sample and complete; between
alternating Wan phases a waiting Save and stop stops the Run at the preview, after the phase's own end save.
"""

import uuid
from pathlib import Path
from typing import Any

from musubi_gui.checkpoint_names import training_saves
from musubi_gui.run_paths import RunPaths
from musubi_gui.run_store import RunStore, utc_now
from musubi_gui.step_offset import segment_step_offset
from musubi_gui.trainer_control import ACTIONS, HANDLED_FILE, REQUEST_FILE, read_json, write_json
from musubi_gui.wan_pair_runtime import is_paired

LABELS = {"save": "Save now", "sample": "Sample now", "save_and_stop": "Save and stop"}
NOT_REACHED = "training ended before its next step."
TRAINING_FINISHED = "training finished before the next step; its final save is the latest."


def request_file(run: dict[str, Any]) -> Path:
    return RunPaths.from_record(run).logs / REQUEST_FILE


def unavailable(run: dict[str, Any], action: str) -> str | None:
    """Why `action` can't be requested for `run` now, or None when it can."""
    if action not in ACTIONS:
        return f"Unknown action {action!r}; choose one of {', '.join(ACTIONS)}."
    if run["status"] != "training":
        return f"{LABELS[action]} acts on a training step; this Run isn't training."
    if pending := run.get("control_request"):
        return f"Wait for {LABELS[pending['action']]} to be applied at the next step."
    if action == "sample":
        if is_paired(run):
            return "Sample now is unavailable: alternating Wan previews run as separate sampling stages."
        if not any("--sample_prompts" in stage["args"] for stage in run["stages"] if stage["name"] == "train"):
            return "Sample now is unavailable: previews are off for this Job."
    return None


def request(store: RunStore, run_id: str, action: str) -> dict[str, Any]:
    """Record and write a request for the training process to apply at its next step."""

    def change(record: dict[str, Any]) -> None:
        if reason := unavailable(record, action):
            raise ValueError(reason)
        pending = {"id": uuid.uuid4().hex, "action": action, "requested": utc_now()}
        # Written inside the record transaction: a failed write leaves no recorded request.
        write_json(request_file(record), {"id": pending["id"], "action": action})
        record["control_request"] = pending

    return store.update(run_id, change)


def collect(store: RunStore, run: dict[str, Any]) -> dict[str, Any] | None:
    """Record what the training process did with the waiting request; returns that history entry."""
    file = RunPaths.from_record(run).logs / HANDLED_FILE
    # Checked several times a second while training, so a missing file costs no record read.
    handled = read_json(file) if file.is_file() else None
    if handled is None:
        return None
    entry: dict[str, Any] = {}

    def change(record: dict[str, Any]) -> None:
        if recorded := next(
            (item for item in record.get("controls", []) if "handled" in item and item["id"] == handled.get("id")), None
        ):
            # Already recorded by a check whose removal of the handled file failed.
            entry.update(recorded)
            return
        pending = record.get("control_request") or {}
        entry.update(
            id=handled.get("id"),
            action=handled.get("action"),
            requested=pending.get("requested") if pending.get("id") == handled.get("id") else None,
            handled=utc_now(),
            step=handled.get("step"),
            **({"error": handled["error"]} if handled.get("error") else {}),
        )
        if pending.get("id") == handled.get("id"):
            record.pop("control_request", None)
        record["controls"] = [*record.get("controls", []), entry]
        if entry["action"] == "save_and_stop" and not entry.get("error") and record["status"] == "training":
            # Saving now; the process exits at its next step.
            record["status"] = "stopping"

    # Recorded before the handled file is removed, so a failed update leaves the outcome for the next check.
    store.update(run["id"], change)
    # Unless a newer request's outcome has already replaced it.
    if (read_json(file) or {}).get("id") == handled.get("id"):
        file.unlink(missing_ok=True)
    return entry


def drop(store: RunStore, run: dict[str, Any], reason: str = NOT_REACHED) -> dict[str, Any] | None:
    """Remove a request no training step will read; returns the dropped request."""
    request_file(run).unlink(missing_ok=True)
    dropped: dict[str, Any] = {}

    def change(record: dict[str, Any]) -> None:
        if pending := record.pop("control_request", None):
            dropped.update(pending)
            record["controls"] = [*record.get("controls", []), {**pending, "dropped": utc_now(), "error": reason}]

    store.update(run["id"], change)
    return dropped or None


def unsaved_steps(run: dict[str, Any], stage: dict[str, Any], local_step: int) -> int | None:
    """Updates of `stage` since its latest save, which Stop now would lose; None when it names no saves.

    A stage starts from a save: the previous phase's state, a restored state, or nothing trained yet.
    """
    if "--output_name" not in stage.get("args", []):
        return None
    saves = training_saves(stage, RunPaths.from_record(run).directory, ".safetensors", segment_step_offset(run, stage))
    return local_step - max((step for step, _ in saves if step <= local_step), default=0)


def stopped_by_save(run: dict[str, Any]) -> bool:
    """Whether a Save and stop was applied, so the training stage's clean exit stops the Run."""
    return any(
        entry["action"] == "save_and_stop" and "handled" in entry and not entry.get("error")
        for entry in run.get("controls", [])
    )

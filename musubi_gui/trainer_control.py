"""Save now, Sample now and Save and stop, honoured by a training process at its next optimizer step.

The backend writes one request at a time to `control-request.json` in the Run folder and passes its path to
training stages in `MUSUBI_CONTROL_REQUEST`. The trainer reads it at the post-optimizer hook, which runs just before
upstream's sample and save checks, so it can make that step sample or save. It then records the outcome in
`control-handled.json` and removes the request. Imported by the app-owned trainer entry point in the managed
environment, so it uses the standard library only.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from musubi_gui.step_offset import StepSchedule

ENVIRONMENT = "MUSUBI_CONTROL_REQUEST"
REQUEST_FILE = "control-request.json"
HANDLED_FILE = "control-handled.json"
ACTIONS = ("save", "sample", "save_and_stop")


def write_json(file: Path, value: object) -> None:
    """Replace `file` in one rename, so a reader never sees part of it."""
    temporary = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    for attempt in range(100):
        try:
            os.replace(temporary, file)
            return
        except PermissionError:
            # Windows refuses the rename while another process has the target open for a moment.
            if attempt == 99:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def read_json(file: Path) -> dict[str, Any] | None:
    """The object in `file`, or None when it is missing or unreadable for now."""
    try:
        value = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class StopAfterSave(SystemExit):
    """Ends the training process cleanly, with exit code zero, after a Save and stop save is written."""

    def __init__(self) -> None:
        super().__init__(0)


class TrainerControl:
    """Applies a pending request to the trainer's arguments for the step being counted.

    Runs after the step schedule has set this step's save and preview intervals, which it overrides for one step
    only: the schedule sets them again before the next. The final step needs no forced save, because upstream's
    train-end save writes it, as `<name>` and `<name>-state`.
    """

    def __init__(self, request_file: Path):
        self.request_file = request_file
        self.stopping = False

    def at_step(self, namespace: Any, local_step: int, step: int) -> None:
        """At the post-optimizer hook of 1-based update `local_step`, which saves and previews are named `step`."""
        if not self.request_file.is_file():
            return
        request = read_json(self.request_file)
        if request is None:
            return
        action = request.get("action")
        handled: dict[str, Any] = {"id": request.get("id"), "action": action, "step": step}
        final = local_step >= int(getattr(namespace, "max_train_steps", 0) or 0)
        if action == "sample":
            if getattr(namespace, "sample_prompts", None):
                namespace.sample_every_n_steps = local_step
            else:
                handled["error"] = "Previews are off for this training process."
        elif action in ("save", "save_and_stop"):
            if action == "save_and_stop":
                # The state is what a later segment resumes from, so it is saved even when the Job doesn't keep state.
                if final:
                    namespace.save_state_on_train_end = True
                else:
                    namespace.save_state = True
                    self.stopping = True
            if not final:
                namespace.save_every_n_steps = local_step
        else:
            handled["error"] = f"Unknown control action {action!r}."
        # Removed first: once the outcome is collected, the backend may write the next request.
        self.request_file.unlink(missing_ok=True)
        write_json(self.request_file.with_name(HANDLED_FILE), handled)
        print(f"Control request {action} handled at step {step}.", flush=True)

    def after_save(self) -> None:
        """At the trainer's next hook after a Save and stop save: end the process."""
        if self.stopping:
            print("Saved; stopping as requested.", flush=True)
            raise StopAfterSave


def post_optimizer_step(
    namespace: Any,
    local_step: int,
    schedule: StepSchedule,
    *,
    expert_step_offset: int | None = None,
    control: TrainerControl | None = None,
) -> None:
    """The app's work at the post-optimizer hook of 1-based update `local_step`, before upstream's save and sample checks.

    The schedule sets this step's save and preview intervals. An alternating Wan phase's final step leaves its save to
    the mandatory train-end save, which writes the same checkpoint and state. Then a control request can make this step
    save or preview, named by the expert's cumulative step in an alternating Wan phase and by global step otherwise.
    """
    schedule.before_step(namespace, local_step)
    if expert_step_offset is not None and local_step == namespace.max_train_steps:
        namespace.save_every_n_steps = None
    if control:
        named = expert_step_offset + local_step if expert_step_offset is not None else schedule.global_step(local_step)
        control.at_step(namespace, local_step, named)

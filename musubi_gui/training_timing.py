"""Small durable wall-clock observations, usable in the managed trainer without app dependencies."""

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def stamp() -> str:
    return datetime.now(UTC).isoformat()


class TrainingTiming:
    def __init__(self, path: Path):
        self.path = path
        self.previous: tuple[int, float] | None = None
        self.preview_started: float | None = None
        self.preview_between_steps = 0.0
        self.data: dict[str, Any] = {
            "version": 1,
            "training": {"step": 0, "intervals": 0, "seconds": 0},
            "previews": {"completed": 0, "seconds": 0, "active": None, "failed": 0},
        }

    def write(self) -> None:
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".timing-", suffix=".json")
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, allow_nan=False)
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            # Timing is advisory. A failed diagnostic must not interrupt training or sampling.
            pass
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def step(self, step: int) -> None:
        now = time.monotonic()
        training = self.data["training"]
        if self.previous:
            previous_step, previous_at = self.previous
            if step <= previous_step:
                return
            training["intervals"] += step - previous_step
            training["seconds"] += max(0.0, now - previous_at - self.preview_between_steps)
        else:
            training["first_step_at"] = stamp()
        training.update(step=step, last_step_at=stamp())
        self.previous = (step, now)
        self.preview_between_steps = 0.0
        self.write()

    def preview_start(self, step: int) -> None:
        self.preview_started = time.monotonic()
        self.data["previews"]["active"] = {"step": step, "started": stamp()}
        self.write()

    def preview_finish(self, *, completed: bool) -> None:
        if self.preview_started is None:
            return
        seconds = max(0.0, time.monotonic() - self.preview_started)
        previews = self.data["previews"]
        if self.previous:
            self.preview_between_steps += seconds
        if completed:
            previews["completed"] += 1
            previews["seconds"] += seconds
            previews["last_finished"] = stamp()
        else:
            previews["failed"] += 1
        previews["active"] = None
        self.preview_started = None
        self.write()

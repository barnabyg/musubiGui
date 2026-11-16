"""Continuous step numbering for a restored segment: the trainer counts from zero, names use global steps.

Imported by the app-owned trainer entry point in the managed environment, so it uses the standard library only.
"""

from typing import Any

ENVIRONMENT = "MUSUBI_STEP_OFFSET"


def segment_step_offset(run: dict[str, Any], stage: dict[str, Any]) -> int:
    """Optimizer updates completed before this segment's training stage started counting.

    Alternating Wan phases name their own saves per expert, so the general offset never applies to them.
    Segments restored before continuous numbering have no recorded offset and keep local step names.
    """
    if stage.get("name") != "train" or stage.get("training"):
        return 0
    return int(run.get("segment", {}).get("step_offset", 0))


def due_steps(interval: int, offset: int, steps: int) -> list[int]:
    """Global steps at which an interval save or preview fires in a segment of `steps` local updates."""
    if interval < 1:
        return []
    first = (offset // interval + 1) * interval
    return list(range(first, offset + steps + 1, interval))


class OffsetStepName:
    """A `str.format` template, as upstream's `STEP_FILE_NAME`, that numbers a save by its global step."""

    def __init__(self, template: str, offset: int):
        self.template = template
        self.offset = offset

    def format(self, name: str, step: int, *rest: Any) -> str:
        return self.template.format(name, self.offset + int(step), *rest)


class StepSchedule:
    """Fires the trainer's interval saves and previews at global multiples after resuming with an offset.

    The pinned trainer checks `local_step % args.save_every_n_steps` and `should_sample_images(args, local_step)`
    right after its post-optimizer hook. Before each step, the hook sets each interval to the local step when its
    global step is due, and to None otherwise, so offset 250 with interval 100 fires at global 300, 400...
    """

    def __init__(self, offset: int):
        self.offset = offset
        self.intervals: dict[str, int | None] | None = None

    def global_step(self, local_step: int) -> int:
        return self.offset + local_step

    def before_step(self, namespace: Any, local_step: int) -> None:
        """`local_step` is the 1-based update the trainer is about to count."""
        if self.intervals is None:
            # Captured before the first change; later steps see the per-step values written below.
            self.intervals = {
                field: getattr(namespace, field, None) for field in ("save_every_n_steps", "sample_every_n_steps")
            }
        for field, interval in self.intervals.items():
            due = bool(interval) and self.global_step(local_step) % int(interval or 1) == 0
            setattr(namespace, field, local_step if due else None)

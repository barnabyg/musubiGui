"""Applies the configured learning rate to an optimizer and scheduler restored from a saved state.

`--resume` loads the saved optimizer and scheduler state after both are built from the arguments, so the restored
param groups' `lr` and `initial_lr` and the scheduler's `base_lrs` keep the learning rate the state was trained with.
A segment resumed with a changed learning rate would silently train with the old one. The trainer entry point
records each param group's configured base learning rate when upstream builds the scheduler, before the state
loads, and writes it back at `on_train_start`, after the state loads and before the first optimizer step.

Imported by the app-owned trainer entry point in the managed environment, so it uses the standard library only.
"""

import math
from typing import Any

PREFIX = "Resume learning rate"


def _base_rates(optimizer: Any, scheduler: Any) -> list[float]:
    """Each param group's base learning rate: what the scheduler scales, before its warmup or decay factor."""
    rates = getattr(scheduler, "base_lrs", None)
    if isinstance(rates, list):
        return [float(rate) for rate in rates]
    return [float(group.get("initial_lr", group["lr"])) for group in optimizer.param_groups]


def _rates(values: list[float]) -> str:
    return ", ".join(f"{value:g}" for value in values)


class LearningRateOverride:
    """The configured base learning rate of each param group, written back over a restored optimizer state.

    Per-group values come from the freshly built optimizer, so upstream's ratios between groups, such as LoRA+'s
    scaled `lora_up` group, follow the configured learning rate rather than being flattened to it.
    """

    def __init__(self) -> None:
        self.configured: list[float] | None = None
        self.scheduler: Any = None

    def capture(self, optimizer: Any, scheduler: Any) -> None:
        """When upstream builds the scheduler from the arguments, before `accelerator.prepare` and `--resume`."""
        self.scheduler = scheduler
        self.configured = _base_rates(optimizer, scheduler)

    def apply(self, *, resuming: bool, learning_rate: float | None, optimizer: Any) -> str | None:
        """After the saved state has loaded and before the first optimizer step; returns the line to log.

        Not resuming, or with no configured learning rate (an optimizer that sets its own), it changes nothing and
        returns None. An unchanged learning rate changes nothing either; its line says so.
        """
        if not resuming or learning_rate is None:
            return None
        if self.configured is None:
            return f"{PREFIX}: not applied; the trainer built no learning rate scheduler to read it from."
        groups = optimizer.param_groups
        saved = _base_rates(optimizer, self.scheduler)
        if len(groups) != len(self.configured) or len(saved) != len(self.configured):
            raise ValueError(
                f"{PREFIX}: the saved state has {len(groups)} optimizer parameter groups and {len(saved)} scheduled "
                f"rates, but this configuration builds {len(self.configured)}."
            )
        if all(math.isclose(old, new, rel_tol=1e-9, abs_tol=0.0) for old, new in zip(saved, self.configured)):
            return f"{PREFIX}: {learning_rate:g}, unchanged from the saved state."
        for group, old, new in zip(groups, saved, self.configured):
            # Keeps the scheduler's current warmup or decay factor; a constant schedule's factor is one.
            factor = float(group["lr"]) / old if old else 1.0
            group["lr"] = new * factor
            group["initial_lr"] = new
        if isinstance(getattr(self.scheduler, "base_lrs", None), list):
            self.scheduler.base_lrs = list(self.configured)
        # The saved counterpart of the configured value, through the first group's ratio to it.
        before = learning_rate * saved[0] / self.configured[0] if self.configured[0] else saved[0]
        line = f"{PREFIX}: applied {learning_rate:g}; the saved state had {before:g}."
        if len(self.configured) > 1:
            line += f" Parameter groups: {_rates(self.configured)} (saved {_rates(saved)})."
        return line

"""Plan sequential Wan expert phases in optimizer updates, independently of subprocesses."""

from dataclasses import asdict, dataclass
from typing import Any, Literal

EXPERTS = ("low", "high")
PAIRED_RECIPE = "alternating"
# Command plans are frozen in the Run. Bound their size even for an accidental million-step N=1 draft.
MAX_PHASES = 10_000


@dataclass(frozen=True)
class ExpertPhase:
    expert: Literal["low", "high"]
    start: int
    steps: int
    low_before: int
    high_before: int

    def record(self) -> dict[str, Any]:
        return asdict(self)


def expert_steps(step: int, interval: int) -> dict[str, int]:
    """Cumulative updates at an absolute point in the low-first schedule."""
    if interval < 1 or step < 0:
        raise ValueError("Expert interval must be positive and the schedule position nonnegative.")
    cycles, remainder = divmod(step, 2 * interval)
    return {
        "low": cycles * interval + min(remainder, interval),
        "high": cycles * interval + max(0, remainder - interval),
    }


def alternating_phases(steps: int, interval: int, *, start: int = 0, preview_every: int = 0) -> list[ExpertPhase]:
    """Plan additional updates, continuing any partially completed phase first."""
    if steps < 1:
        raise ValueError("Choose a positive number of optimizer steps.")
    if preview_every < 0:
        raise ValueError("Preview interval: enter zero (off) or a positive number of optimizer steps.")
    expert_steps(start, interval)
    if (start + steps - 1) // interval - start // interval + 1 > MAX_PHASES:
        raise ValueError(f"Expert interval: increase it to keep the Run within {MAX_PHASES} phases.")
    phases: list[ExpertPhase] = []
    position = start
    while position < start + steps:
        if len(phases) >= MAX_PHASES:
            raise ValueError(
                f"Preview interval: increase it or the expert interval to stay within {MAX_PHASES} phases."
            )
        counts = expert_steps(position, interval)
        phase_steps = min(interval - position % interval, start + steps - position)
        if preview_every:
            phase_steps = min(phase_steps, preview_every - position % preview_every)
        phases.append(
            ExpertPhase(
                "low" if (position // interval) % 2 == 0 else "high",
                position,
                phase_steps,
                counts["low"],
                counts["high"],
            )
        )
        position += phase_steps
    return phases


def paired_phases(
    steps: int,
    interval: int,
    *,
    save_every: int,
    preview_every: int = 0,
    start: int = 0,
    counts: dict[str, int] | None = None,
) -> list[ExpertPhase]:
    """Train both experts to the same target, meeting at save/preview boundaries.

    Starts remain raw update positions for compatibility with recorded Runs.
    """
    if steps < 1 or interval < 1 or save_every < 1 or preview_every < 0 or start < 0:
        raise ValueError("Expert interval: steps and save/switch intervals must be positive.")
    current = dict(counts or {"low": 0, "high": 0})
    target = max(current.values()) + steps
    phases: list[ExpertPhase] = []
    position = start
    while min(current.values()) < target:
        expert: Literal["low", "high"] = "low" if current["low"] <= current["high"] else "high"
        before = current[expert]
        end = min(target, (before // interval + 1) * interval, (before // save_every + 1) * save_every)
        if preview_every:
            end = min(end, (before // preview_every + 1) * preview_every)
        other = current["high" if expert == "low" else "low"]
        if other > before:
            end = min(end, other)
        if len(phases) >= MAX_PHASES:
            raise ValueError(
                f"Expert interval: increase save, preview or switch intervals to stay within {MAX_PHASES} phases."
            )
        phases.append(ExpertPhase(expert, position, end - before, current["low"], current["high"]))
        position += end - before
        current[expert] = end
    return phases


def phase_counts(phase: dict[str, Any], steps: int) -> dict[str, int]:
    """Read counts from a frozen phase, including older schedules."""
    if "low_before" not in phase:
        return expert_steps(phase["start"] + steps, phase["interval"])
    counts = {expert: phase[f"{expert}_before"] for expert in EXPERTS}
    counts[phase["expert"]] += steps
    return counts

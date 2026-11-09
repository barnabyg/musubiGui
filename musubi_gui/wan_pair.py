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


def alternating_phases(steps: int, interval: int, *, start: int = 0) -> list[ExpertPhase]:
    """Plan additional updates, continuing any partially completed phase first."""
    if steps < 1:
        raise ValueError("Choose a positive number of optimizer steps.")
    expert_steps(start, interval)
    if (start + steps - 1) // interval - start // interval + 1 > MAX_PHASES:
        raise ValueError(f"Expert interval: increase it to keep the Run within {MAX_PHASES} phases.")
    phases = []
    position = start
    while position < start + steps:
        counts = expert_steps(position, interval)
        phase_steps = min(interval - position % interval, start + steps - position)
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

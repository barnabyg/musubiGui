"""Training progress as upstream reports it; anything it did not report stays unknown."""

import math
import re
from typing import Any

# Upstream's tqdm bar, as in `steps:  10%|█   | 60/600 [1:19:00<11:51:00, 79.00s/it, avr_loss=0.0812]`, rewrites one
# line with carriage returns. Requiring the line to start with `steps:` keeps logged arguments from matching.
STEP = re.compile(r"(?:^|[\r\n])steps:[^\r\n]*?(\d+)/(\d+)([^\r\n]*)")
TIMES = re.compile(r"\[([\d:]+)<([\d:]+)")
LOSS = re.compile(r"\b(?:avr_)?loss=([-+\d.eE]+)")
RATE = re.compile(r"(?<![\w.+-])([\d.]+)\s*(s/it|it/s)\b")


def estimated_remaining(progress: dict[str, Any]) -> int | None:
    """Extrapolate the trainer's observed speed across remaining segment steps."""
    remaining = int(progress["total"]) - int(progress["step"])
    if remaining == 0:
        return 0
    step = int(progress.get("phase_step", progress["step"]))
    total = int(progress.get("phase_total", progress["total"]))
    if step <= 0:
        return None
    for field, divisor in [("eta", total - step), ("elapsed", step)]:
        value = progress.get(field)
        if value is None or divisor <= 0:
            continue
        seconds = 0
        for part in value.split(":"):
            seconds = seconds * 60 + int(part)
        if seconds > 0:
            return math.ceil(seconds / divisor * remaining)
    return None


def parse_progress(text: str) -> dict[str, Any] | None:
    """Return the latest reported step, or None when the log has not reported one."""
    latest = None
    for match in STEP.finditer(text):
        latest = match
    if latest is None or int(latest[2]) < 1 or int(latest[1]) > int(latest[2]):
        return None
    rest = latest[3]
    times = TIMES.search(rest)
    loss = LOSS.search(rest)
    rate = RATE.search(rest)
    seconds_per_step = None
    if rate:
        try:
            value = float(rate[1])
            if value > 0 and math.isfinite(value):
                seconds_per_step = value if rate[2] == "s/it" else 1 / value
        except ValueError:
            pass
    return {
        "step": int(latest[1]),
        "total": int(latest[2]),
        "elapsed": times[1] if times else None,
        "eta": times[2] if times else None,
        "loss": loss[1] if loss else None,
        "seconds_per_step": seconds_per_step,
    }

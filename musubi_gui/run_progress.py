"""Training progress as upstream reports it; anything it did not report stays unknown."""

import re
from typing import Any

# Upstream's tqdm bar, as in `steps:  10%|█   | 60/600 [1:19:00<11:51:00, 79.00s/it, avr_loss=0.0812]`, rewrites one
# line with carriage returns. Requiring the line to start with `steps:` keeps logged arguments from matching.
STEP = re.compile(r"(?:^|[\r\n])steps:[^\r\n]*?(\d+)/(\d+)([^\r\n]*)")
TIMES = re.compile(r"\[([\d:]+)<([\d:]+)")
LOSS = re.compile(r"\b(?:avr_)?loss=([-+\d.eE]+)")


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
    return {
        "step": int(latest[1]),
        "total": int(latest[2]),
        "elapsed": times[1] if times else None,
        "eta": times[2] if times else None,
        "loss": loss[1] if loss else None,
    }

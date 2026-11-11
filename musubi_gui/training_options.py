"""Training controls supported by the selected existing environment."""

import json
import subprocess  # nosec B404
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from musubi_gui.settings import Settings

if TYPE_CHECKING:
    from musubi_gui.jobs import Job


def timestep_range_policy(job: "Job") -> Literal["original", "bounded", "shared-av"]:
    if job.family == "h3":
        return "shared-av"
    config = job.config
    return (
        "original"
        if config.timestep_sampling == "shift" and config.discrete_flow_shift == 3.0 and config.sigmoid_scale == 1.0
        else "bounded"
    )


def optimizer_options(settings: Settings, *, fixture_mode: bool = False) -> dict[str, Any]:
    options = {
        "optimizers": ["AdamW"],
        "reason": "AdamW8bit needs bitsandbytes with CUDA in the selected training Python.",
    }
    if fixture_mode:
        return {"optimizers": ["AdamW", "AdamW8bit"], "reason": "Synthetic fixture: optimizer execution is simulated."}
    if not settings.python or not Path(settings.python).is_file():
        return options
    try:
        # B603: fixed read-only probe through explicitly selected training Python; no shell.
        result = subprocess.run(
            [settings.python, "-I", "-B", str(Path(__file__).with_name("optimizer_probe.py"))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )  # nosec B603
        lines = [line for line in result.stdout.splitlines() if line.startswith("MUSUBI_OPTIMIZERS=")]
        if result.returncode == 0 and lines and json.loads(lines[-1].split("=", 1)[1]).get("adamw8bit"):
            return {
                "optimizers": ["AdamW", "AdamW8bit"],
                "reason": "AdamW8bit is available in the selected training Python.",
            }
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return options

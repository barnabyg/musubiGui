"""Locate training saves using either legacy phase names or completed expert updates."""

import re
from pathlib import Path
from typing import Any


def expert_step_name(final_name: str, offset: int, step: int) -> str:
    return f"{final_name.rsplit('-step', 1)[0]}-step{offset + step:08d}"


def training_saves(stage: dict[str, Any], output: Path, suffix: str) -> list[tuple[int, Path]]:
    """Return local step positions without inferring progress from a legacy phase identifier."""
    args = stage["args"]
    name = args[args.index("--output_name") + 1]
    total = int(args[args.index("--max_train_steps") + 1])
    offset = stage.get("training", {}).get("checkpoint_step_offset")
    stem = name.rsplit("-step", 1)[0] if offset is not None else name
    saves = []
    pattern = re.compile(re.escape(stem) + r"-step(\d+)" + re.escape(suffix))
    for path in output.glob(f"{stem}*{suffix}"):
        match = pattern.fullmatch(path.name)
        if match:
            step = int(match[1]) - (offset or 0)
        elif offset is None and path.name == name + suffix:
            step = total
        else:
            continue
        if 0 < step <= total:
            saves.append((step, path))
    return sorted(saves)

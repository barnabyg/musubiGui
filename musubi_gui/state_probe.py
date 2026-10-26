"""Read optimizer counters using the selected existing training environment."""

import json
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    import torch  # type: ignore[import-not-found]

    state = Path(sys.argv[1])
    try:
        # weights_only deliberately refuses arbitrary pickle globals. Never relax this for unknown state.
        optimizer: Any = torch.load(state / "optimizer.bin", map_location="cpu", weights_only=True)
        scheduler: Any = torch.load(state / "scheduler.bin", map_location="cpu", weights_only=True)
        steps = [int(value.get("step", 0)) for value in optimizer.get("state", {}).values()]
        if not steps or max(steps) < 1 or not isinstance(scheduler, dict):
            raise ValueError("No saved optimizer steps or scheduler state.")
        print(
            "MUSUBI_STATE="
            + json.dumps({"valid": True, "optimizer_step": max(steps), "scheduler_step": scheduler.get("last_epoch")})
        )
    except Exception as error:
        print(
            "MUSUBI_STATE="
            + json.dumps(
                {
                    "valid": False,
                    "error": f"State validation failed ({type(error).__name__}); the original state is preserved.",
                }
            )
        )


if __name__ == "__main__":
    main()

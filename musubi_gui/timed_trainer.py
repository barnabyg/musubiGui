"""App-owned entry point: observe pinned trainer hooks without modifying upstream source."""

import argparse
import importlib
import importlib.util
import os
import runpy
import sys
from pathlib import Path
from typing import Any

# Accelerate executes this file in the separate managed environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musubi_gui.training_timing import TrainingTiming  # noqa: E402


def observe_trainer(trainer: Any, timing: TrainingTiming) -> None:
    before = trainer.on_before_sample_images
    sample = trainer.sample_images
    after = trainer.on_after_sample_images
    optimizer_step = trainer.on_post_optimizer_step
    succeeded = False

    def on_before(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal succeeded
        succeeded = False
        timing.preview_start(int(args[3]))
        return before(self, *args, **kwargs)

    def on_sample(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal succeeded
        result = sample(self, *args, **kwargs)
        namespace, step = args[1], int(args[3])
        # Upstream can return successfully when no prompt/output exists.
        try:
            succeeded = any(
                file.stat().st_size > 0
                for file in (Path(namespace.output_dir) / "sample").glob(f"*_{step:06d}_*")
                if file.suffix.lower() in {".mp4", ".png"}
            )
        except OSError:
            succeeded = False
        return result

    def on_after(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = after(self, *args, **kwargs)
        except BaseException:
            timing.preview_finish(completed=False)
            raise
        timing.preview_finish(completed=succeeded)
        return result

    def on_optimizer_step(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = optimizer_step(self, *args, **kwargs)
        if args[4]:
            timing.step(int(args[5]) + 1)
        return result

    trainer.on_before_sample_images = on_before
    trainer.sample_images = on_sample
    trainer.on_after_sample_images = on_after
    trainer.on_post_optimizer_step = on_optimizer_step


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timing-file", type=Path, required=True)
    parser.add_argument("--family", choices=["wan", "h3"], required=True)
    parser.add_argument("--upstream", required=True)
    options, remaining = parser.parse_known_args()
    os.environ["MUSUBI_RUN_TIMING_FILE"] = str(options.timing_file)
    if importlib.util.find_spec("musubi_tuner") is not None:
        name = "wan_train_network" if options.family == "wan" else "minimax_h3_train_network"
        module = importlib.import_module("musubi_tuner." + name)
        trainer = module.WanNetworkTrainer if options.family == "wan" else module.MiniMaxH3NetworkTrainer
        observe_trainer(trainer, TrainingTiming(options.timing_file))
    sys.argv = [options.upstream, *remaining]
    runpy.run_path(options.upstream, run_name="__main__")


if __name__ == "__main__":
    main()

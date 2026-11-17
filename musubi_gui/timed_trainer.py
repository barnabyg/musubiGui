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
from musubi_gui.checkpoint_names import expert_step_name  # noqa: E402
from musubi_gui.resume_learning_rate import LearningRateOverride  # noqa: E402
from musubi_gui.step_offset import ENVIRONMENT, OffsetStepName, StepSchedule  # noqa: E402
from musubi_gui.trainer_control import ENVIRONMENT as CONTROL_ENVIRONMENT  # noqa: E402
from musubi_gui.trainer_control import TrainerControl, post_optimizer_step  # noqa: E402
from musubi_gui.training_timing import TrainingTiming  # noqa: E402


def override_learning_rate(trainer: Any) -> None:
    """Train a resumed state with the configured learning rate rather than the one restored with it.

    Upstream builds the optimizer and scheduler from the arguments in `get_lr_scheduler`, then `--resume` loads the
    saved state over them in `resume_from_local_or_hf_if_specified`. That method gets neither object, and its caller
    is private, so the configured values are recorded when the scheduler is built and written back at
    `on_train_start`: the public hook that runs after the state loads and before the first optimizer step.
    """
    override = LearningRateOverride()
    build = trainer.get_lr_scheduler
    start = trainer.on_train_start

    def on_get_lr_scheduler(self: Any, args: Any, optimizer: Any, *rest: Any, **kwargs: Any) -> Any:
        scheduler = build(self, args, optimizer, *rest, **kwargs)
        override.capture(optimizer, scheduler)
        return scheduler

    def on_train_start(
        self: Any, args: Any, accelerator: Any, network: Any, transformer: Any, optimizer: Any, *rest: Any
    ) -> Any:
        result = start(self, args, accelerator, network, transformer, optimizer, *rest)
        line = override.apply(
            resuming=bool(getattr(args, "resume", None)),
            learning_rate=getattr(args, "learning_rate", None),
            optimizer=optimizer,
        )
        if line:
            print(line, flush=True)
        return result

    trainer.get_lr_scheduler = on_get_lr_scheduler
    trainer.on_train_start = on_train_start


def observe_trainer(
    trainer: Any,
    timing: TrainingTiming,
    *,
    expert_step_offset: int | None = None,
    step_offset: int = 0,
    control: TrainerControl | None = None,
) -> None:
    before = trainer.on_before_sample_images
    sample = trainer.sample_images
    after = trainer.on_after_sample_images
    optimizer_step = trainer.on_post_optimizer_step
    # Sets each step's intervals; offset zero fires them as upstream does. A control request overrides one step.
    schedule = StepSchedule(step_offset)
    succeeded = False

    def global_step(step: int) -> int:
        # Step zero is upstream's optional sample before training, not an update of this segment.
        return schedule.global_step(step) if step else step

    def on_before(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal succeeded
        succeeded = False
        timing.preview_start(global_step(int(args[3])))
        return before(self, *args, **kwargs)

    def on_sample(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal succeeded
        namespace, step = args[1], global_step(int(args[3]))
        if step != int(args[3]):
            # Preview filenames use the steps argument. Upstream rechecks it against the interval, so the
            # interval briefly equals the global step being sampled.
            interval = namespace.sample_every_n_steps
            namespace.sample_every_n_steps = step
            try:
                result = sample(self, *args[:3], step, *args[4:], **kwargs)
            finally:
                namespace.sample_every_n_steps = interval
        else:
            result = sample(self, *args, **kwargs)
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
            step = int(args[5]) + 1
            timing.step(global_step(step))
            namespace = args[0]
            if expert_step_offset is not None:
                namespace.output_name = expert_step_name(namespace.output_name, expert_step_offset, step)
            post_optimizer_step(namespace, step, schedule, expert_step_offset=expert_step_offset, control=control)
        return result

    def stop_after_save(original: Any) -> Any:
        # The next hook after a Save and stop step: its save has been written.
        def hook(self: Any, *args: Any, **kwargs: Any) -> Any:
            if control:
                control.after_save()
            return original(self, *args, **kwargs)

        return hook

    trainer.on_before_sample_images = on_before
    trainer.sample_images = on_sample
    trainer.on_after_sample_images = on_after
    trainer.on_post_optimizer_step = on_optimizer_step
    if hasattr(trainer, "get_lr_scheduler") and hasattr(trainer, "on_train_start"):
        override_learning_rate(trainer)
    # The next step starts with its batch; an epoch that ends first calls its end hook.
    for name in ("process_batch", "on_epoch_end"):
        if hasattr(trainer, name):
            setattr(trainer, name, stop_after_save(getattr(trainer, name)))


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
        offset = os.environ.get("MUSUBI_EXPERT_STEP_OFFSET") if options.family == "wan" else None
        # Alternating Wan phases already name saves by cumulative expert step.
        step_offset = int(os.environ.get(ENVIRONMENT) or 0) if offset is None else 0
        if offset is not None or step_offset:
            train_utils: Any = importlib.import_module("musubi_tuner.utils.train_utils")
        if offset is not None:
            # Pinned trainer formats these at the save boundary. output_name already
            # includes the cumulative expert step; str.format ignores the local step.
            train_utils.STEP_FILE_NAME = "{}"
            train_utils.STEP_STATE_NAME = "{}-state"
        elif step_offset:
            # Formatted with the local step at each save; the final `<name>` and `<name>-state` stay unnumbered.
            train_utils.STEP_FILE_NAME = OffsetStepName(train_utils.STEP_FILE_NAME, step_offset)
            train_utils.STEP_STATE_NAME = OffsetStepName(train_utils.STEP_STATE_NAME, step_offset)
        observe_trainer(
            trainer,
            TrainingTiming(options.timing_file),
            expert_step_offset=int(offset) if offset is not None else None,
            step_offset=step_offset,
            control=TrainerControl(Path(request)) if (request := os.environ.get(CONTROL_ENVIRONMENT)) else None,
        )
    sys.argv = [options.upstream, *remaining]
    runpy.run_path(options.upstream, run_name="__main__")


if __name__ == "__main__":
    main()

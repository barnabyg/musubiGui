"""Resolve alternating Wan phases to ordinary pinned-trainer commands."""

import copy
from pathlib import Path
from typing import Any

from musubi_gui.recipe import Stage
from musubi_gui.wan_pair import alternating_phases


def argument(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def set_argument(args: list[str], flag: str, value: str) -> None:
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args.extend([flag, value])


def paired_stages(
    stages: list[Stage],
    models: dict[str, str],
    steps: int,
    interval: int,
    *,
    start: int = 0,
    sources: dict[str, Any] | None = None,
    preview_every: int = 0,
    final_sample: bool = True,
) -> list[Stage]:
    """Each phase has its own output name; no later phase can overwrite an earlier state."""
    template = next(stage for stage in stages if stage.name == "train")
    output = Path(argument(template.args, "--output_dir"))
    stem = argument(template.args, "--output_name")
    states = copy.deepcopy(sources or {})
    result = [copy.deepcopy(stage) for stage in stages if stage.name not in {"train", "generate"}]
    checkpoints: dict[str, dict[str, Any]] = {}
    generation = next((stage for stage in stages if stage.name == "generate"), None)

    def sample(step: int | None) -> None:
        if generation is None:
            return
        generated = copy.deepcopy(generation)
        generated.preview_step = step
        for expert, flag in [("low", "--lora_weight"), ("high", "--lora_weight_high_noise")]:
            selected = checkpoints.get(expert) or (sources or {}).get(expert, {}).get("checkpoint")
            if not selected:
                if step is None:
                    raise ValueError("Sampling requires a trained checkpoint for both experts.")
                if flag in generated.args:
                    index = generated.args.index(flag)
                    del generated.args[index : index + 2]
            else:
                set_argument(generated.args, flag, selected["path"])
        if preview_every:
            destination = output.parent / "samples" / (f"step{step:08d}" if step is not None else "final")
            set_argument(generated.args, "--save_path", str(destination))
        result.append(generated)

    for phase in alternating_phases(steps, interval, start=start, preview_every=preview_every):
        args = list(template.args)
        name = f"{stem}-{phase.expert}-phase{phase.start:08d}"
        for flag, value in [
            ("--dit", models[f"wan_{phase.expert}"]),
            ("--min_timestep", "0" if phase.expert == "low" else "875"),
            ("--max_timestep", "875" if phase.expert == "low" else "1000"),
            ("--max_train_steps", str(phase.steps)),
            ("--output_name", name),
        ]:
            set_argument(args, flag, value)
        # State is mandatory at switches even if the interval exceeds the checkpoint cadence.
        for flag in ("--save_state", "--save_state_on_train_end"):
            if flag not in args:
                args.append(flag)
        if phase.expert in states:
            set_argument(args, "--resume", states[phase.expert]["path"])
        else:
            if "--resume" in args:
                index = args.index("--resume")
                del args[index : index + 2]
        checkpoint = str(output / f"{name}.safetensors")
        before = phase.low_before if phase.expert == "low" else phase.high_before
        checkpoints[phase.expert] = {"path": checkpoint, "steps": before + phase.steps}
        states[phase.expert] = {"path": str(output / f"{name}-state")}
        result.append(Stage("train", args, {**phase.record(), "interval": interval, "checkpoint": checkpoint}))
        end = phase.start + phase.steps
        if preview_every and end % preview_every == 0:
            sample(end)
    if final_sample:
        sample(None)
    return result

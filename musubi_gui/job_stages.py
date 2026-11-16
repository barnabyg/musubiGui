"""Deterministic commands for a Job under the pinned LoRA recipe."""

from pathlib import Path

from musubi_gui.h3 import add_image_flags, h3_stages
from musubi_gui.job_models import Job
from musubi_gui.recipe import Stage
from musubi_gui.run_paths import job_folder
from musubi_gui.sampling import sample_geometry
from musubi_gui.settings import Settings
from musubi_gui.wan import wan_stages
from musubi_gui.wan_pair import PAIRED_RECIPE


def effective_settings(job: Job, settings: Settings) -> Settings:
    return settings.model_copy(update={"models": {**settings.models, **job.config.components}})


def _set_argument(args: list[str], flag: str, value: str) -> None:
    if flag in args:
        args[args.index(flag) + 1] = value
    else:
        args.extend([flag, value])


def build_stages(job: Job, settings: Settings, run: Path, cache: Path, kinds: set[str]) -> list[Stage]:
    """Kinds are the included item kinds, because H3 images change its commands."""
    from musubi_gui.preview_memory import resolve_preview_memory
    from musubi_gui.training_options import timestep_range_policy

    settings = effective_settings(job, settings)
    config = job.config
    output_name = f"{job_folder(job.name)}-r{job.revision}"
    recipe_stages = wan_stages if job.family == "wan" else h3_stages
    sample_width, sample_height, sample_frames = sample_geometry(config)
    stages = [stage for stage in recipe_stages(settings, run, cache) if stage.name != "fixture"]
    if job.family == "h3":
        add_image_flags(stages, kinds)
    for stage in stages:
        args = stage.args
        if stage.name == "train":
            for flag, value in [
                ("--network_dim", config.rank),
                ("--network_alpha", config.alpha),
                ("--learning_rate", config.learning_rate),
                ("--optimizer_type", config.optimizer),
                ("--max_train_steps", config.steps),
                ("--gradient_accumulation_steps", config.gradient_accumulation),
                ("--blocks_to_swap", config.blocks_to_swap),
                ("--save_every_n_steps", config.save_every),
                ("--seed", config.seed),
                ("--output_name", output_name),
            ]:
                _set_argument(args, flag, str(value))
            if job.family == "wan":
                _set_argument(args, "--timestep_sampling", config.timestep_sampling)
                if config.timestep_sampling in {"shift", "sigmoid"}:
                    _set_argument(args, "--sigmoid_scale", str(config.sigmoid_scale))
                if config.timestep_sampling == "shift":
                    _set_argument(args, "--discrete_flow_shift", str(config.discrete_flow_shift))
                elif "--discrete_flow_shift" in args:
                    index = args.index("--discrete_flow_shift")
                    del args[index : index + 2]
                # Keep the original qualified recipe exact; custom distributions use bounded mapping.
                # Upstream rejection sampling falls back outside the expert range after 1000 misses.
                if timestep_range_policy(job) != "original" and "--preserve_distribution_shape" in args:
                    args.remove("--preserve_distribution_shape")
            if config.save_state:
                args.extend(["--save_state", "--save_state_on_train_end"])
            if config.preview_every and config.recipe != PAIRED_RECIPE:
                from musubi_gui.preview import preview_arguments

                args.extend(preview_arguments(job, settings, run))
                stage.preview_memory = resolve_preview_memory(job, settings, native=True)
        if stage.name == "generate":
            if job.family == "wan" and config.sample_frames == 1:
                _set_argument(args, "--output_type", "images")
            _set_argument(args, "--prompt", config.sample_prompt)
            _set_argument(args, "--infer_steps", str(config.sample_steps))
            _set_argument(args, "--seed", str(config.seed))
            stage.preview_memory = resolve_preview_memory(job, settings, native=False)
            _set_argument(args, "--blocks_to_swap", str(stage.preview_memory["blocks_to_swap"]))
            _set_argument(args, "--video_length", str(sample_frames))
            index = args.index("--video_size")
            args[index + 1 : index + 3] = [str(sample_height), str(sample_width)]
            _set_argument(args, "--lora_weight", str(run / "checkpoints" / f"{output_name}.safetensors"))
            if stage.preview_memory["mode"] == "automatic":
                args[4:4] = [
                    str(Path(__file__).with_name("preview_generate.py")),
                    "--fallback-blocks",
                    str(stage.preview_memory["recovery"]["blocks_to_swap"]),
                    "--",
                ]
    stages = [
        stage
        for stage in stages
        if stage.name != "generate"
        or config.sample_enabled
        or (config.recipe == PAIRED_RECIPE and config.preview_every > 0)
    ]
    if job.family == "wan" and config.recipe == PAIRED_RECIPE:
        from musubi_gui.wan_pair_plan import paired_stages

        return paired_stages(
            stages,
            settings.models,
            config.steps,
            config.expert_interval,
            preview_every=config.preview_every,
            final_sample=config.sample_enabled,
            per_expert=config.wan_steps_per_expert,
        )
    return stages

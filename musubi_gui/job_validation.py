"""Configuration checks that decide whether a Job's settings can train under the pinned recipe."""

import math

from musubi_gui.job_models import Job
from musubi_gui.wan_pair import MAX_PHASES, PAIRED_RECIPE, alternating_phases, paired_phases

# Highest block-swap values that trained on the target PC; the next value up fails (docs/reference).
WAN_MAX_BLOCKS_TO_SWAP = 38
H3_MAX_BLOCKS_TO_SWAP = 48


def configuration_errors(job: Job, *, has_videos: bool = True) -> list[str]:
    config = job.config
    sampling = config.sample_enabled or config.preview_every > 0
    needs_frames = has_videos
    errors: list[str] = []
    if sampling:
        multiple = 16 if job.family == "wan" else 32
        for dimension in ("width", "height"):
            value = getattr(config, "sample_" + dimension)
            if value is not None and (value < 64 or value % multiple):
                errors.append(f"Sample {dimension}: enter at least 64, in multiples of {multiple}.")
        frames = (
            config.sample_frames if config.sample_frames is not None else (config.frames if not has_videos else None)
        )
        if frames is not None:
            if job.family == "wan" and (frames < 1 or (frames - 1) % 4):
                errors.append("Sample duration: Wan requires 4*n+1 frames, or one frame for an image sample.")
            elif job.family == "h3" and (not 124 <= frames <= 345 or (frames - 5) % 17):
                errors.append("Sample duration: H3 requires 17*n+5 frames between 124 and 345 (24 fps).")
    if job.family == "h3" and config.timestep_sampling != "uniform":
        errors.append("Timestep strategy: the pinned H3 trainer supports uniform sampling only.")
    if job.family == "wan":
        if config.timestep_sampling in {"shift", "sigmoid"} and not 0.001 <= config.sigmoid_scale <= 1000:
            errors.append("Sigmoid scale: enter a finite number from 0.001 to 1000 for stable sampling.")
        if config.timestep_sampling == "shift" and not 0.001 <= config.discrete_flow_shift <= 1000:
            errors.append("Flow shift: enter a finite number from 0.001 to 1000 for stable sampling.")
    if not job.name.strip():
        errors.append("Name: enter a job name.")
    listed = [entry.dataset_id for entry in job.datasets]
    if not listed:
        errors.append("Dataset: select a registered dataset.")
    if len(set(listed)) != len(listed):
        errors.append("Dataset: the same dataset is listed more than once; remove the extra row.")
    if job.family == "wan":
        if config.recipe not in {"low-noise", PAIRED_RECIPE}:
            errors.append("Recipe: choose alternating high/low Wan training.")
        if config.recipe == PAIRED_RECIPE:
            if config.expert_interval < 1:
                errors.append("Expert interval: enter a positive integer.")
            elif math.ceil(config.steps / config.expert_interval) > MAX_PHASES:
                errors.append(f"Expert interval: increase it to keep the Run within {MAX_PHASES} phases.")
            elif not config.wan_steps_per_expert and config.steps <= config.expert_interval:
                errors.append(
                    "Steps: paired training must include at least one update of each expert; increase steps or reduce the expert interval."
                )
            if not config.save_state:
                errors.append("Training state: alternating experts requires saved optimizer/training state.")
        if config.width < 64 or config.height < 64 or config.width % 16 or config.height % 16:
            errors.append("Geometry: Wan width and height must be at least 64 and multiples of 16.")
        if needs_frames and (config.frames < 1 or (config.frames - 1) % 4):
            errors.append("Frames: Wan requires 4*n+1 frames.")
        if not 0 <= config.blocks_to_swap <= WAN_MAX_BLOCKS_TO_SWAP:
            errors.append(
                f"Block swapping: select between 0 and {WAN_MAX_BLOCKS_TO_SWAP} Wan blocks. "
                "39 fails at step 0 in the pinned upstream FP8 linear patch."
            )
        if not config.fp8_scaled:
            errors.append("Memory: runtime FP8 scaling from native FP16 weights is required by this Wan recipe.")
        if config.block_swap_h2d_only:
            errors.append("Memory: H2D-only swapping belongs to the H3 recipe, not the initial Wan recipe.")
        if has_videos and config.frame_extraction == "slide" and config.frame_stride < 1:
            errors.append("Frame extraction: enter a slide stride of at least one frame.")
        if has_videos and config.frame_extraction == "uniform" and config.frame_sample < 2:
            errors.append("Frame extraction: uniform sampling needs at least two windows per clip; use head for one.")
        allowed_components = {"wan_low", "wan_high", "wan_vae", "wan_t5"}
    else:
        if config.recipe != "guidance-loss":
            errors.append(
                "Recipe: initial H3 T2VA supports guidance loss only; other recipes need separate validation."
            )
        if config.width < 64 or config.height < 64 or config.width % 32 or config.height % 32:
            errors.append("Geometry: H3 width and height must be at least 64 and multiples of 32.")
        if needs_frames and (not 124 <= config.frames <= 345 or (config.frames - 5) % 17):
            errors.append("Frames: H3 requires 17*n+5 frames in the released 124–345 range.")
        if not 0 <= config.blocks_to_swap <= H3_MAX_BLOCKS_TO_SWAP:
            errors.append(
                f"Block swapping: select between 0 and {H3_MAX_BLOCKS_TO_SWAP} of the 50 H3 blocks; "
                "the pinned upstream rejects higher values."
            )
        if config.fp8_scaled:
            errors.append("Memory: H3 does not support FP8 transformer weights/runtime scaling.")
        if not config.block_swap_h2d_only:
            errors.append("Memory: H3 guidance loss requires H2D-only swapping for the validated target-PC recipe.")
        if has_videos and config.frame_extraction != "head":
            errors.append(
                "Frame extraction: H3 uses head only. Audio alignment and coverage are inspected for each clip's first window, so other modes are not qualified."
            )
        allowed_components = {"h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"}
    if config.batch_size != 1:
        errors.append(
            "Batch size: the target-PC recipe supports one; use gradient accumulation for effective batch size."
        )
    for field, value in [
        ("repeats", config.repeats),
        ("rank", config.rank),
        ("alpha", config.alpha),
        ("steps", config.steps),
        ("gradient accumulation", config.gradient_accumulation),
        ("checkpoint interval", config.save_every),
        ("sample steps", config.sample_steps),
    ]:
        if value < 1:
            errors.append(f"{field.capitalize()}: enter a positive integer.")
    if not math.isfinite(config.learning_rate) or not 0 < config.learning_rate <= 1:
        errors.append("Learning rate: enter a finite number greater than zero and at most one.")
    if not config.gradient_checkpointing:
        errors.append("Memory: gradient checkpointing is required by this target-PC recipe.")
    if config.offload_inactive_dit:
        errors.append(
            "Memory: inactive-DiT offload is outside this single-expert recipe and conflicts with block swapping."
        )
    if config.preview_every < 0:
        errors.append("Preview interval: enter zero (off) or a positive number of optimizer steps.")
    if config.keep_checkpoints < 0:
        errors.append("Checkpoint retention: enter zero (unlimited) or a positive number of saves.")
    if config.recipe == PAIRED_RECIPE and config.steps > 0 and config.expert_interval > 0 and config.save_every > 0:
        try:
            if config.wan_steps_per_expert:
                paired_phases(
                    config.steps,
                    config.expert_interval,
                    save_every=config.save_every,
                    preview_every=config.preview_every,
                )
            else:
                alternating_phases(config.steps, config.expert_interval, preview_every=config.preview_every)
        except ValueError as error:
            errors.append(str(error))
    if (config.sample_enabled or config.preview_every) and not config.sample_prompt.strip():
        errors.append("Sampling: enter a prompt or turn off both final sampling and in-run previews.")
    if any(key not in allowed_components for key in config.components):
        errors.append("Components: only the four supported component keys for the selected model are accepted.")
    return errors

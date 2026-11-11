"""Frozen native trainer prompts; sampling runs inside the existing training process."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from musubi_gui.media_probe import inspect
from musubi_gui.sampling import sample_geometry
from musubi_gui.settings import Settings

if TYPE_CHECKING:
    from musubi_gui.jobs import Job


def preview_prompt(job: "Job") -> list[dict[str, Any]]:
    config = job.config
    width, height, frames = sample_geometry(config)
    return [
        {
            "prompt": config.sample_prompt,
            "seed": config.seed,
            "width": width,
            "height": height,
            "frame_count": frames,
            "sample_steps": config.sample_steps,
        }
    ]


def preview_arguments(job: "Job", settings: Settings, run: Path) -> list[str]:
    args = ["--sample_every_n_steps", str(job.config.preview_every), "--sample_prompts", str(run / "previews.json")]
    if job.family == "wan":
        args.extend(
            ["--t5", settings.models["wan_t5"], "--fp8_t5", "--vae", settings.models["wan_vae"], "--vae_cache_cpu"]
        )
    else:
        args.extend(
            [
                "--text_encoder",
                settings.models["h3_text"],
                "--text_encoder_blocks_to_swap",
                "50",
                "--video_vae",
                settings.models["h3_video_vae"],
                "--audio_vae",
                settings.models["h3_audio_vae"],
            ]
        )
    return args


def preview_step(file: Path) -> int | None:
    # save_images_grid appends a three-digit frame index to one-frame PNG samples.
    match = re.search(r"_(\d{6,})_\d{2}_\d{14}(?:_-?\d+)?(?:_\d{3})?$", file.stem)
    return int(match[1]) if match else None


def validate_previews(args: list[str], family: str) -> None:
    """A trainer's success exit alone does not establish that requested previews exist."""
    if "--sample_every_n_steps" not in args:
        return
    interval = int(args[args.index("--sample_every_n_steps") + 1])
    steps = int(args[args.index("--max_train_steps") + 1])
    directory = Path(args[args.index("--output_dir") + 1]) / "sample"
    found = {preview_step(file): file for file in directory.glob("*") if file.suffix.lower() in {".mp4", ".png"}}
    for step in range(interval, steps + 1, interval):
        if step not in found:
            raise ValueError(f"Training exited successfully but the preview at step {step} is missing.")
        metadata = inspect(found[step])
        if family == "h3" and (not metadata.get("frames") or not metadata.get("audio")):
            raise ValueError(f"H3 preview at step {step} needs decodable video and audio.")

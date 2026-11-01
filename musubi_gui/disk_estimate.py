"""Expected disk use of a Run from sizes measured on the target PC (docs/jobs.md lists the limits)."""

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from musubi_gui.settings import Settings

if TYPE_CHECKING:
    from musubi_gui.jobs import JobConfig

GB = 1000**3
MINIMUM_MARGIN = GB
# Temporary files, logs and caches the estimate does not itemize.
MARGIN_FRACTION = 0.1
# LoRA bytes are linear in rank: Wan rank 8 and 16, H3 rank 4 and 8 (docs/reference).
CHECKPOINT_BYTES = {"wan": (136_456, 19_169_495), "h3": (69_832, 18_636_932)}
# AdamW state holds the weights and two moments: 920,958,520 / 306,848,376 and 447,704,425 / 149,165,288.
STATE_TO_CHECKPOINT = 3.0014
# Mean text cache per item and Hugging Face files per Run in the #26 trial.
TEXT_CACHE_BYTES = {"wan": 397_532, "h3": 491_904}
HUGGING_FACE_BYTES = {"wan": 21_508_498, "h3": 11_505_839}
# Largest measured sample per pixel-frame: the 256x256x17 Wan smoke sample.
SAMPLE_BYTES_PER_PIXEL_FRAME = 0.25
# Fixture-mode only, so a browser test can report low free space.
FREE_SPACE_OVERRIDE = "MUSUBI_TEST_FREE_BYTES"


def _latent_bytes(family: str, config: "JobConfig", frames: int) -> int:
    width, height = max(0, config.width), max(0, config.height)
    if family == "wan":
        # 16 channels at 1/8 scale, one latent per four frames, two bytes per value and a safetensors header.
        return 2 * 16 * ((max(1, frames) - 1) // 4 + 1) * (height // 8) * (width // 8) + 216
    if frames == 1:
        # An image is one latent token of 48 channels at 1/16 scale and two bytes per value, the same rate as
        # the video fit below, plus its two silent audio frames and header. Measured at 640x352 only (85,980 bytes).
        return int(0.375 * width * height) + 1_500
    # Fitted to 640x352 and 832x480 at 124 frames, including the audio latents; frame scaling is unmeasured.
    return int((13.875 * width * height + 53_860) * max(0, frames) / 124)


def estimate_disk(family: str, config: "JobConfig", items: list[dict[str, Any]], *, media_bytes: int) -> dict[str, Any]:
    """Peak bytes per category. A final save on the interval counts until the duplicate is removed after training."""
    latents = 0
    for item in items:
        if item.get("kind") == "image":
            latents += _latent_bytes(family, config, 1)
        else:
            # A clip without a window count still caches once when it has an unknown length.
            windows = item.get("windows")
            latents += _latent_bytes(family, config, config.frames) * (1 if windows is None else windows)
    caches = latents + TEXT_CACHE_BYTES[family] * len(items) + HUGGING_FACE_BYTES[family]
    saves = config.steps // config.save_every + 1 if config.steps >= 1 and config.save_every >= 1 else 0
    offset, per_rank = CHECKPOINT_BYTES[family]
    checkpoint = offset + per_rank * max(0, config.rank)
    checkpoints = saves * checkpoint
    states = int(checkpoints * STATE_TO_CHECKPOINT) if config.save_state else 0
    samples = (
        int(max(0, config.width) * max(0, config.height) * max(0, config.frames) * SAMPLE_BYTES_PER_PIXEL_FRAME)
        if config.sample_enabled
        else 0
    )
    return {
        "media_snapshot": media_bytes,
        "caches": caches,
        "checkpoints": checkpoints,
        "states": states,
        "samples": samples,
        "saves": saves,
        "output_root": media_bytes + checkpoints + states + samples,
        "cache_root": caches,
    }


def format_bytes(value: int) -> str:
    return f"{value / GB:.1f} GB" if value >= 100_000_000 else f"{value / 1000**2:.0f} MB"


def free_space_reader(*, fixture_mode: bool = False) -> Callable[[Path], int]:
    override = os.environ.get(FREE_SPACE_OVERRIDE) if fixture_mode else None
    if override:
        return lambda _: int(override)
    return lambda path: shutil.disk_usage(path).free


def _existing_ancestor(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return path


def _volume_key(path: Path) -> object:
    try:
        return os.stat(path).st_dev
    except OSError:
        return path


def check_volumes(settings: Settings, estimate: dict[str, Any], *, free: Callable[[Path], int]) -> list[dict[str, Any]]:
    """Free space per target volume; roots sharing a volume are checked against their combined need."""
    volumes: dict[object, dict[str, Any]] = {}
    for label, value, needed in [
        ("Output root", settings.output_root, estimate["output_root"]),
        ("Cache root", settings.cache_root, estimate["cache_root"]),
    ]:
        if not value:
            continue
        existing = _existing_ancestor(Path(value))
        volume = volumes.setdefault(
            _volume_key(existing),
            {"volume": existing.resolve().anchor or str(existing), "path": existing, "roots": [], "needed": 0},
        )
        volume["roots"].append(label)
        volume["needed"] += needed
    results = []
    for volume in volumes.values():
        path, needed, roots = volume.pop("path"), volume["needed"], ", ".join(volume["roots"])
        margin = max(MINIMUM_MARGIN, int(needed * MARGIN_FRACTION))
        try:
            available: int | None = free(path)
        except OSError:
            available = None
        shortfall = 0 if available is None else max(0, needed + margin - available)
        warning = ""
        if available is None:
            warning = f"Free space on {volume['volume']} ({roots}) could not be read, so this Run's estimated {format_bytes(needed)} was not checked."
        elif shortfall:
            warning = (
                f"Low disk space on {volume['volume']} ({roots}): this Run needs about {format_bytes(needed + margin)} "
                f"including a {format_bytes(margin)} margin and {format_bytes(available)} is free, {format_bytes(shortfall)} short."
            )
        results.append({**volume, "margin": margin, "free": available, "shortfall": shortfall, "warning": warning})
    return results

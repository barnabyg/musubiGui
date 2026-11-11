"""Resolve additive sample overrides without changing legacy Job geometry."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from musubi_gui.jobs import JobConfig


def sample_geometry(config: "JobConfig") -> tuple[int, int, int]:
    return (
        config.width if config.sample_width is None else config.sample_width,
        config.height if config.sample_height is None else config.sample_height,
        config.frames if config.sample_frames is None else config.sample_frames,
    )


def native_preview_frames(family: str, config: "JobConfig") -> int:
    frames = sample_geometry(config)[2]
    # Pinned trainer_base.save_sample writes native Wan video at 24 fps.
    # Leave legacy Jobs' frame counts unchanged, including one-frame PNGs.
    return (
        max(5, 4 * round((frames * 24 / 16 - 1) / 4) + 1)
        if family == "wan" and config.sample_frames is not None and frames > 1
        else frames
    )

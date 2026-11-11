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

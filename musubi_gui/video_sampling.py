"""Mirror the pinned musubi-tuner video loader: Wan frame-rate resampling and cached frame windows."""

from pathlib import Path
from typing import Any

WAN_FPS = 16.0
# Rates this close to 16 fps drop no frames for any practical clip length; they are not reported as resampled.
RATE_TOLERANCE = 0.01


def resampled_frames(source_frames: int, source_fps: float) -> int:
    """Pinned UPSTREAM media_utils.load_video keeps a decoded frame whenever the accumulated
    target/source index advances, so frames are dropped (never added) to reach 16 fps."""
    delta = WAN_FPS / source_fps
    position, previous, count = 0.0, -1, 0
    for _ in range(source_frames):
        index = int(position)
        position += delta
        if index != previous:
            previous = index
            count += 1
    return count


def window_starts(
    frame_count: int, target: int, extraction: str = "head", stride: int = 1, sample: int = 1
) -> list[int]:
    """Distinct crop positions the pinned dataset caches for one clip (UPSTREAM image_video_dataset.py).

    Repeated uniform positions share one cache file, so they count once.
    """
    if frame_count < target:
        return []
    if extraction == "chunk":
        return list(range(0, frame_count - target + 1, target))
    if extraction == "slide":
        return list(range(0, frame_count - target + 1, stride))
    if extraction == "uniform" and sample > 1:
        # numpy.linspace(0, span, sample, dtype=int) truncates evenly spaced positions and ends exactly at span.
        span = frame_count - target
        return sorted({int(index * (span / (sample - 1))) for index in range(sample - 1)} | {span})
    return [0]


def source_frames(metadata: dict[str, Any]) -> int | None:
    frames, fps, duration = metadata.get("frames"), metadata.get("fps"), metadata.get("duration")
    if isinstance(frames, int) and frames > 0:
        return frames
    # Some containers do not record a frame count; estimate it from the probed duration.
    if isinstance(fps, (int, float)) and isinstance(duration, (int, float)) and fps > 0 and duration > 0:
        return round(duration * fps)
    return None


def source_rate(item: dict[str, Any]) -> float | None:
    fps = item.get("metadata", {}).get("fps")
    return float(fps) if isinstance(fps, (int, float)) and fps > 0 else None


def source_rates(items: list[dict[str, Any]]) -> list[float]:
    """Distinct probed video rates; each becomes its own Wan dataset section because `source_fps` is per section."""
    return sorted({rate for item in items if item["kind"] == "video" and (rate := source_rate(item)) is not None})


def video_sections(directory: Path, cache: Path, rates: list[float]) -> list[tuple[float | None, Path, Path]]:
    """(source_fps, video directory, cache directory) per Wan video section.

    Sections never share a cache directory: the pinned cachers delete files that their own section did not produce.
    """
    if len(rates) <= 1:
        return [(rates[0] if rates else None, directory, cache / "dataset")]
    return [(rate, directory / f"rate-{index}", cache / f"dataset-rate-{index}") for index, rate in enumerate(rates, 1)]


def video_directory(directory: Path, rates: list[float], rate: float | None) -> Path:
    for section_rate, section_directory, _ in video_sections(directory, Path(), rates):
        if section_rate == rate:
            return section_directory
    raise ValueError("A video's frame rate is unknown; rescan the dataset before preparation.")


def rate_label(fps: float) -> str:
    return f"{fps:.6g} fps"


def plan_windows(
    family: str,
    frames: int,
    items: list[dict[str, Any]],
    *,
    extraction: str = "head",
    stride: int = 1,
    sample: int = 1,
) -> dict[str, Any]:
    """Cached windows per included video, with the errors and warnings resampling causes for Wan."""
    errors: list[str] = []
    warnings: list[str] = []
    planned: list[dict[str, Any]] = []

    def windows(frame_count: int) -> int | None:
        # Review reports an invalid stride as a configuration error; it has no window count.
        if extraction == "slide" and stride < 1:
            return None
        return len(window_starts(frame_count, frames, extraction, stride, sample))

    for item in items:
        if item["kind"] != "video":
            continue
        name, rate, count = item["name"], source_rate(item), source_frames(item.get("metadata", {}))
        entry: dict[str, Any] = {
            "id": item["id"],
            "name": name,
            "source_fps": rate,
            "source_frames": count,
            "resampled_frames": count,
            "windows": None,
        }
        planned.append(entry)
        if family != "wan":
            # H3 resamples to 24 fps by timestamps; its duration and audio checks are separate.
            if count is not None:
                entry["windows"] = windows(count)
            continue
        if rate is None:
            errors.append(
                f"{name}: the frame rate is unknown, so it cannot be resampled to 16 fps. Re-encode it at a constant frame rate or exclude it."
            )
            continue
        if rate < WAN_FPS - RATE_TOLERANCE:
            errors.append(
                f"{name}: {rate_label(rate)} is below Wan's 16 fps. The pinned loader cannot add frames, so it would train sped up. Re-encode at 16 fps or higher, or exclude it."
            )
            continue
        if count is None:
            continue
        resampled = resampled_frames(count, rate)
        entry.update(resampled_frames=resampled, windows=windows(resampled))
        if abs(rate - WAN_FPS) > RATE_TOLERANCE:
            warnings.append(
                f"{name}: {rate_label(rate)} is resampled to 16 fps by dropping frames ({count} → {resampled} frames, {resampled / WAN_FPS:.2f} s)."
            )
        if entry["windows"] == 0:
            errors.append(
                f"{name}: {count} frames at {rate_label(rate)} resample to {resampled} frames ({resampled / WAN_FPS:.2f} s at 16 fps); {frames} frames need {frames / WAN_FPS:.2f} s. Shorten the job or choose a longer video."
            )
    rates = source_rates(items) if family == "wan" else []
    if len(rates) > 1:
        warnings.append(
            f"Videos use {len(rates)} frame rates ({', '.join(rate_label(rate) for rate in rates)}); each rate gets its own dataset section so every clip resamples to 16 fps."
        )
    total = sum(entry["windows"] or 0 for entry in planned)
    return {"errors": errors, "warnings": warnings, "items": planned, "total": total, "source_rates": rates}

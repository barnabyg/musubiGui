"""Turn a job and its items into native dataset sections, the one source of the reviewed and the executed TOML."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from musubi_gui.video_sampling import source_rate, source_rates, video_sections

if TYPE_CHECKING:
    from musubi_gui.jobs import Job


@dataclass
class Section:
    dataset_id: str
    kind: Literal["image", "video"]
    # Absent for images, for H3 (which resamples by timestamps) and for an empty section.
    fps: float | None
    # Absent when the dataset row leaves repeats to the job's default.
    repeats: int | None
    directory: Path
    cache: Path
    # H3 video sections list their clips, with explicit audio, in their own metadata file.
    jsonl: Path | None = None
    items: list[dict[str, Any]] = field(default_factory=list)


def plan_sections(job: "Job", items: list[dict[str, Any]], run: Path, cache: Path) -> list[Section]:
    """Sections for a Run folder, in job order. Items must carry their `dataset_id`.

    One dataset keeps its folders at the top of the Run; several use `media/set-N` and `dataset-N` caches.
    Sections never share a cache directory: the pinned cachers delete files their own section did not produce.
    """
    several = len(job.datasets) > 1
    sections: list[Section] = []
    for number, entry in enumerate(job.datasets, 1):
        members = [item for item in items if item.get("dataset_id") == entry.dataset_id]
        directory = run / "media" / f"set-{number}" if several else run / "media"
        prefix = f"dataset-{number}" if several else "dataset"
        images = [item for item in members if item["kind"] == "image"]
        if images:
            sections.append(
                Section(
                    entry.dataset_id, "image", None, entry.repeats, directory, cache / f"{prefix}-images", None, images
                )
            )
        videos = [item for item in members if item["kind"] == "video"]
        if videos:
            rates = source_rates(videos) if job.family == "wan" else []
            for rate, video_directory, video_cache in video_sections(directory, cache, rates, prefix):
                clips = [clip for clip in videos if not rates or source_rate(clip) == rate]
                jsonl = run / f"{prefix}.jsonl" if job.family == "h3" else None
                sections.append(
                    Section(entry.dataset_id, "video", rate, entry.repeats, video_directory, video_cache, jsonl, clips)
                )
    if not sections:
        # Nothing to train yet, so review previews the form a video section takes.
        jsonl = run / "dataset.jsonl" if job.family == "h3" else None
        sections.append(Section("", "video", None, None, run / "media", cache / "dataset", jsonl))
    return sections


def section_folders(sections: list[Section]) -> dict[str, Path]:
    """Where each item's media goes, by item id."""
    return {item["id"]: section.directory for section in sections for item in section.items}


def layout_identity(job: "Job", items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What several datasets add to a cache's identity: the folders it fills, and which media goes in each."""
    return [
        {
            "cache": section.cache.as_posix(),
            "kind": section.kind,
            "fps": section.fps,
            "repeats": section.repeats,
            "media": [item["sha256"] for item in section.items],
        }
        for section in plan_sections(job, items, Path(), Path())
    ]


def section_balance(
    job: "Job", sections: list[Section], windows: list[dict[str, Any]], names: dict[str, str]
) -> list[dict[str, Any]]:
    """Each section's samples per epoch: a cached video window counts as one sample, as does an image.

    A clip of unknown length still caches once, as the disk estimate assumes.
    """
    counts = {entry["id"]: entry["windows"] for entry in windows}

    def samples(item: dict[str, Any]) -> int:
        if item["kind"] == "image":
            return 1
        count = counts.get(item["id"])
        return 1 if count is None else int(count)

    rows: list[dict[str, Any]] = []
    for section in sections:
        if not section.items:
            continue
        repeats = job.config.repeats if section.repeats is None else section.repeats
        rows.append(
            {
                "dataset_id": section.dataset_id,
                "dataset": names.get(section.dataset_id, section.dataset_id),
                "kind": section.kind,
                "fps": section.fps,
                "items": len(section.items),
                "repeats": repeats,
                "samples": max(0, repeats) * sum(samples(item) for item in section.items),
            }
        )
    total = sum(row["samples"] for row in rows)
    return [{**row, "share": row["samples"] / total if total else 0.0} for row in rows]


def dataset_toml(job: "Job", sections: list[Section]) -> str:
    """Directories are an immutable managed snapshot, never the mutable source dataset.

    Wan videos resample to 16 fps from each section's `source_fps`; H3 resamples by timestamps and ignores it.
    """
    config = job.config

    def quote(path: Path) -> str:
        return json.dumps(path.as_posix(), ensure_ascii=False)

    def native(section: Section) -> str:
        repeats = f"num_repeats = {section.repeats}\n" if section.repeats is not None else ""
        if section.kind == "image":
            return (
                f"[[datasets]]\nimage_directory = {quote(section.directory)}\n"
                f'cache_directory = {quote(section.cache)}\ncaption_extension = ".txt"\n' + repeats
            )
        source = (
            f"video_jsonl_file = {quote(section.jsonl)}"
            if section.jsonl
            else f"video_directory = {quote(section.directory)}"
        )
        return (
            f"[[datasets]]\n{source}\ncache_directory = {quote(section.cache)}\n"
            f'caption_extension = ".txt"\ntarget_frames = [{config.frames}]\n'
            f'frame_extraction = "{config.frame_extraction}"\n'
            + (f"frame_stride = {config.frame_stride}\n" if config.frame_extraction == "slide" else "")
            + (f"frame_sample = {config.frame_sample}\n" if config.frame_extraction == "uniform" else "")
            + (f"source_fps = {section.fps!r}\n" if section.fps is not None else "")
            + repeats
        )

    general = (
        f"[general]\nresolution = [{config.width}, {config.height}]\nbatch_size = {config.batch_size}\n"
        f"num_repeats = {config.repeats}\nenable_bucket = {str(config.enable_bucket).lower()}\n"
        f"bucket_no_upscale = {str(config.bucket_no_upscale).lower()}\n\n"
    )
    return general + "\n".join(native(section) for section in sections)

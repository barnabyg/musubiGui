"""Review checks of a Job's listed datasets and their included items."""

from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from musubi_gui.job_models import Job
from musubi_gui.training_sections import dataset_resolution
from musubi_gui.video_sampling import plan_windows

# Pinned UPSTREAM src/musubi_tuner/dataset/media_utils.py. JPEG-XL is optional
# upstream and is not indexed by this application's gallery.
TRAINING_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif"})
TRAINING_VIDEOS = frozenset({".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"})
# A shape this close to the job's loses too little to a crop to be worth a warning.
ASPECT_TOLERANCE = 0.01


class ReviewMessages(list[str]):
    """Text stays compatible with API clients; targets are indexed so identical messages can link to different items."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__(messages)
        self.targets: dict[int, dict[str, str | list[dict[str, str]]]] = {}

    def add(self, message: str, **target: str | list[dict[str, str]]) -> None:
        self.targets[len(self)] = target
        self.append(message)


def review_items(
    store: Any,
    job: Job,
    dataset: dict[str, Any],
    prefix: str,
    errors: ReviewMessages,
    warnings: ReviewMessages,
    audio_coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Check one listed dataset's included items; returns them labelled with the dataset they came from."""
    dataset_id = dataset["id"]
    items = [
        {**item, "dataset_id": dataset_id} for item in store.items(dataset_id) if not item["excluded"] and item["valid"]
    ]
    if not items:
        errors.add(f'Dataset "{dataset["name"]}": no valid, included media remain.', dataset_id=dataset_id)
    for item in items:
        name = prefix + item["name"]
        target_item = {"dataset_id": dataset_id, "item_id": item["id"]}
        suffix = Path(item["path"]).suffix.lower()
        supported = TRAINING_IMAGES if item["kind"] == "image" else TRAINING_VIDEOS
        if suffix not in supported:
            target = "PNG, JPEG, WebP or BMP" if item["kind"] == "image" else "MP4"
            errors.add(
                f"{name}: {suffix} {item['kind']} files can be previewed but are not supported by the pinned {job.family.upper()} training recipe. Convert to {target} or exclude this item before preparation.",
                view="preview",
                **target_item,
            )
        if not item["caption"]["exists"]:
            warnings.add(
                f"{name}: no caption sidecar; the frozen caption will be empty.", view="caption", **target_item
            )
        frames = item.get("metadata", {}).get("frames")
        # Wan duration is checked after resampling to 16 fps, in plan_windows.
        if job.family == "h3" and item["kind"] == "video" and isinstance(frames, int) and frames < job.config.frames:
            errors.add(
                f"{name}: {frames} decoded frames cannot supply the requested {job.config.frames}; shorten the job or choose a longer video.",
                view="preview",
                **target_item,
            )
        if job.family == "h3" and item["kind"] == "video":
            audio_coverage["included"] += 1
            try:
                inspection: dict[str, Any] = store.inspect_h3(dataset_id, item["id"], frames=job.config.frames)
            except (ValueError, OSError, HTTPException) as error:
                errors.add(f"{name}: {error}", view="audio", **target_item)
                continue
            audio_coverage["items"].append({"id": item["id"], "name": item["name"], **inspection})
            for message in inspection.get("errors", []):
                errors.add(f"{name}: {message}", view="audio", **target_item)
            for message in inspection.get("warnings", []):
                warnings.add(f"{name}: {message}", view="audio", **target_item)
            if inspection.get("source") == "missing":
                audio_coverage["missing"] += 1
                if job.config.audio_policy == "require":
                    errors.add(
                        f"{name}: audio policy requires supervised audio; supply valid aligned audio or allow missing audio explicitly.",
                        view="audio",
                        **target_item,
                    )
                else:
                    warnings.add(
                        f"{name}: absent audio is allowed and excluded from audio supervision; no audio is synthesized.",
                        view="audio",
                        **target_item,
                    )
            elif not inspection.get("errors"):
                audio_coverage["supervised"] += 1
    warnings.extend(_reshaped_image_warning(job, dataset, items))
    if job.family == "h3" and not job.config.enable_bucket:
        width, height = dataset_resolution(job, dataset["id"])
        count = sum(
            1
            for item in items
            if item["kind"] == "video"
            and item.get("metadata", {}).get("width")
            and item.get("metadata", {}).get("height")
            and width > 0
            and height > 0
            and abs(item["metadata"]["width"] / item["metadata"]["height"] / (width / height) - 1) > ASPECT_TOLERANCE
        )
        if count:
            warnings.append(
                f'Dataset "{dataset["name"]}": {count} video(s) differ from the fixed {width}×{height} training shape. '
                "Training will centre-crop them and may cut off heads or other subjects. "
                "Enable bucketing with no upscaling to preserve the prepared portrait and landscape shapes. "
                "Portrait and mixed-orientation H3 GPU training have not been measured on the target PC."
            )
    return items


def _reshaped_image_warning(job: Job, dataset: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    """Bucketing off leaves the pinned loader one bucket: image_video_dataset.py scales to cover it and centre-crops."""
    config = job.config
    width, height = dataset_resolution(job, dataset["id"])
    if config.enable_bucket or width < 1 or height < 1:
        return []
    target = width / height

    def reshaped(item: dict[str, Any]) -> bool:
        width, height = item.get("metadata", {}).get("width"), item.get("metadata", {}).get("height")
        return bool(width and height) and abs(width / height / target - 1) > ASPECT_TOLERANCE

    count = sum(1 for item in items if item["kind"] == "image" and reshaped(item))
    if not count:
        return []
    images = "1 image has" if count == 1 else f"{count} images have"
    return [
        f'Dataset "{dataset["name"]}": {images} a different shape from {width}×{height} and bucketing is '
        "off, so training scales each to cover that size and crops the overflow from the centre. "
        + (
            "Enable bucketing to keep the whole image."
            if job.family == "wan"
            else "Bucketing with H3 images has not been tried on the target PC, so crop or resize them to this shape instead."
        )
    ]


def plan_job_windows(
    job: Job,
    datasets: list[dict[str, Any]],
    items: list[dict[str, Any]],
    errors: ReviewMessages,
    warnings: ReviewMessages,
) -> dict[str, Any]:
    """plan_windows within each dataset, because frame-rate sections and their warnings belong to one dataset."""
    config = job.config
    merged: dict[str, Any] = {"items": [], "total": 0, "source_rates": []}
    for dataset in datasets or [{"id": "", "name": ""}]:
        prefix = f'Dataset "{dataset["name"]}": ' if len(job.datasets) > 1 else ""
        plan = plan_windows(
            job.family,
            config.frames,
            [item for item in items if item["dataset_id"] == dataset["id"]],
            extraction=config.frame_extraction,
            stride=config.frame_stride,
            sample=config.frame_sample,
        )
        for level, messages in [("errors", errors), ("warnings", warnings)]:
            for item_id, message in plan[level]:
                if item_id:
                    messages.add(prefix + message, dataset_id=dataset["id"], item_id=item_id, view="preview")
                else:
                    messages.append(prefix + message)
        merged["items"].extend(plan["items"])
        merged["total"] += plan["total"]
        merged["source_rates"] = sorted({*merged["source_rates"], *plan["source_rates"]})
    return merged


def add_duplicate_warnings(
    datasets: list[dict[str, Any]], items: list[dict[str, Any]], warnings: ReviewMessages
) -> None:
    """Name files that train twice because two listed datasets hold the same content."""
    names = {dataset["id"]: dataset["name"] for dataset in datasets}
    by_size: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        try:
            by_size.setdefault(Path(item["absolute_path"]).stat().st_size, []).append(item)
        except OSError:
            # Review reports unreadable media through the dataset checks.
            continue
    content_hash = import_module("musubi_gui.datasets").content_hash
    by_content: dict[str, list[dict[str, Any]]] = {}
    # Only files whose size repeats in another dataset can match, so most are never read.
    for group in by_size.values():
        if len({item["dataset_id"] for item in group}) > 1:
            for item in group:
                by_content.setdefault(content_hash(Path(item["absolute_path"])), []).append(item)
    for group in by_content.values():
        if len({item["dataset_id"] for item in group}) < 2:
            continue
        summary = "Identical files in several datasets train more than once"
        copies = [
            {
                "label": f'{item["name"]} in "{names[item["dataset_id"]]}"',
                "dataset_id": item["dataset_id"],
                "item_id": item["id"],
            }
            for item in group
        ]
        warnings.add(f"{summary}: " + ", ".join(copy["label"] for copy in copies) + ".", summary=summary, items=copies)

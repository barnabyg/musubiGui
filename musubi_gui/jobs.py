"""Versioned job drafts and deterministic plans for the pinned LoRA recipe."""

import json
import math
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from musubi_gui.diagnostics import upstream_revision
from musubi_gui.disk_estimate import check_volumes, estimate_disk, free_space_reader
from musubi_gui.h3 import validate_h3
from musubi_gui.job_models import Job
from musubi_gui.job_stages import build_stages, effective_settings
from musubi_gui.job_validation import configuration_errors
from musubi_gui.recipe import UPSTREAM, Stage
from musubi_gui.redaction import redact
from musubi_gui.run_paths import job_folder
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.sampling import native_preview_frames, sample_geometry
from musubi_gui.settings import Settings, validate_paths, within_root
from musubi_gui.training_sections import dataset_resolution, dataset_toml, plan_sections, section_balance
from musubi_gui.video_sampling import plan_windows
from musubi_gui.wan import validate_wan
from musubi_gui.wan_pair import MAX_PHASES, PAIRED_RECIPE, alternating_phases, expert_steps, paired_phases

# Pinned UPSTREAM src/musubi_tuner/dataset/media_utils.py. JPEG-XL is optional
# upstream and is not indexed by this application's gallery.
TRAINING_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif"})
TRAINING_VIDEOS = frozenset({".mp4", ".webm", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"})


# Below this a LoRA has made no visible change to samples in the owner's experience on the target PC.
LOW_STEPS_WARNING = 600
H3_IMAGE_WARNINGS = (
    "H3 images: upstream calls this one-frame training experimental, and using the result for video is unvalidated. "
    "Short image-only and mixed runs completed on the target PC (about 5–6 s per image step, 85,980 bytes per image cache "
    "at 640×352), which proves mechanics and not learning.",
    "H3 images: upstream advises stating the absence of sound in each image's caption, such as a sound: field describing "
    "a silent still. Captions belong to the dataset, so editing them affects every other model using it. This application doesn't change them.",
)
# Above this share one section can swamp the others without notice, such as 200 images beside 10 clips.
BALANCE_WARNING_SHARE = 0.8


# A shape this close to the job's loses too little to a crop to be worth a warning.
ASPECT_TOLERANCE = 0.01


def disk_preflight(
    job: Job,
    settings: Settings,
    items: list[dict[str, Any]],
    video_windows: list[dict[str, Any]],
    *,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    media_bytes = 0
    for item in items:
        try:
            media_bytes += Path(item["absolute_path"]).stat().st_size
        except OSError:
            # Review reports unreadable media through the dataset checks.
            continue
    images = [item for item in items if item["kind"] == "image"]
    sizes = {item["id"]: dataset_resolution(job, item.get("dataset_id", "")) for item in items}
    estimated_items = [
        {**item, "resolution": sizes.get(item["id"], (job.config.width, job.config.height))}
        for item in [*images, *video_windows]
    ]
    estimate = estimate_disk(job.family, job.config, estimated_items, media_bytes=media_bytes)
    volumes = check_volumes(settings, estimate, free=free_space_reader(fixture_mode=fixture_mode))
    return {"estimate": estimate, "volumes": volumes}


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


def review_job(root: Path, job: Job, settings: Settings, *, fixture_mode: bool = False) -> dict[str, Any]:
    from musubi_gui.training_options import timestep_range_policy

    errors = ReviewMessages([])
    warnings = ReviewMessages(
        [
            "Wan has short-run execution evidence on the 96 GB host using the documented native and converted weights; other artifacts and larger workloads need qualification."
            if job.family == "wan"
            else "H3 has short-run execution evidence on the 96 GB host; training quality and larger workloads remain unqualified.",
            "Changes to geometry, duration or memory controls need their own capacity validation.",
        ]
    )
    if 0 < job.config.steps < LOW_STEPS_WARNING:
        warnings.append(
            f"Training steps: {job.config.steps} steps can check that training runs, but expect no visible change "
            f"in samples below about {LOW_STEPS_WARNING} steps."
        )
    if job.config.preview_every and job.config.recipe != PAIRED_RECIPE:
        warnings.append(
            "In-run previews pause training for inference and decoding, retain the optimizer and dataset position, and need separate GPU capacity qualification. Text encoding happens before the training model loads; VAEs are kept on CPU between previews."
        )
        if job.family == "wan":
            warnings.append(
                "Wan in-run previews use only the trained low-noise expert across all sampling timesteps; they are diagnostic previews and differ from the final two-expert sample."
            )
    if job.family == "wan" and job.config.recipe == PAIRED_RECIPE:
        if job.config.preview_every:
            warnings.append(
                "Paired previews end a training phase, release its GPU process, sample both experts, then reload optimizer state. Extra boundaries restart the shuffled dataset pass. Experts without updates use their base weights, labelled with zero trained steps."
            )
        warnings.append(
            "Alternating experts: native GPU qualification covers 256×256, 17 frames, rank 8 and N=1. Useful learning and production-size paired capacity remain unqualified."
        )
    if job.family == "h3" and job.config.width * job.config.height > 640 * 352:
        warnings.append(
            "Geometry: H3 is qualified up to 640×352 on the target PC; 832×480 ran out of VRAM at the first step."
        )
    settings = effective_settings(job, settings)
    if job.config.optimizer == "AdamW8bit":
        from musubi_gui.training_options import optimizer_options

        options = optimizer_options(settings, fixture_mode=fixture_mode)
        if "AdamW8bit" not in options["optimizers"]:
            errors.add(f"Optimizer: {options['reason']}", field="optimizer")
    for label, value in [("Output root", settings.output_root), ("Cache root", settings.cache_root)]:
        if not value:
            errors.append(f"{label}: configure a dedicated directory in Settings.")
    try:
        validate_paths(settings)
        if fixture_mode:
            marker = Path(settings.checkout) / ".musubi-gui-fixture.json"
            if json.loads(marker.read_text(encoding="utf-8")) != {"version": 1, "kind": "synthetic-worker"}:
                errors.append("Fixture marker is not the expected synthetic-worker format.")
            component_names = (
                ["wan_low", "wan_high", "wan_vae", "wan_t5"]
                if job.family == "wan"
                else ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]
            )
            for name in component_names:
                if not settings.models.get(name) or not Path(settings.models[name]).is_file():
                    errors.append(f"Fixture component missing: {name}.")
            warnings.insert(0, "SYNTHETIC FIXTURE: these subprocesses do not establish GPU/model support.")
        else:
            errors.extend(validate_wan(settings) if job.family == "wan" else validate_h3(settings))
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors.append(f"Model paths/format: {error}")
    model_provenance: dict[str, dict[str, str]] = {}
    for name, value in settings.models.items():
        if not name.startswith(job.family + "_") or not value.endswith(".safetensors"):
            continue
        try:
            file = within_root(Path(value), [Path(location) for location in settings.model_roots])
            metadata = safetensors_header(file).get("__metadata__", {})
            if isinstance(metadata, dict):
                model_provenance[name] = {
                    key: redact(item)
                    for key, item in metadata.items()
                    if isinstance(key, str) and isinstance(item, str)
                }
                precision = model_provenance[name].get("source_precision", "")
                if "lossy" in precision.lower():
                    warnings.append(f"{name}: {precision}")
        except (ValueError, OSError, TypeError):
            # Format/path diagnostics are already reported above; provenance never grants compatibility.
            continue
    actual_revision = ""
    try:
        actual_revision = upstream_revision(Path(settings.checkout))
        if actual_revision != UPSTREAM and not fixture_mode:
            errors.append(
                "Upstream revision differs from the pinned recipe. Validate that revision before preparation."
            )
    except (ValueError, OSError) as error:
        errors.append(f"Installation: {error}")
    if not settings.python or not Path(settings.python).is_file():
        errors.append("Training Python: install the supported Musubi version in Settings.")
    effective_items: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    audio_coverage: dict[str, Any] = {"included": 0, "supervised": 0, "missing": 0, "items": []}
    if job.datasets:
        store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
        for entry in job.datasets:
            try:
                dataset = store.get(entry.dataset_id)
            except KeyError:
                errors.add(
                    f'Dataset "{entry.dataset_id}": this dataset is no longer registered. Remove it from the job.',
                    field=f"remove-dataset-{entry.dataset_id}",
                )
                continue
            datasets.append(dataset)
            if entry.resolution is not None:
                multiple = 16 if job.family == "wan" else 32
                for dimension, dimension_value in zip(("width", "height"), entry.resolution, strict=True):
                    if dimension_value < 64 or dimension_value % multiple:
                        errors.add(
                            f'Dataset "{dataset["name"]}": resolution {dimension} must be at least 64 and a multiple of {multiple}.',
                            field=f"resolution-{entry.dataset_id}-{dimension}",
                        )
            if entry.repeats is not None and entry.repeats < 1:
                errors.add(
                    f'Dataset "{dataset["name"]}": enter repeats of at least one, or leave them blank to use the job\'s.',
                    field=f"dataset-{entry.dataset_id}",
                )
            prefix = f'Dataset "{dataset["name"]}": ' if len(job.datasets) > 1 else ""
            try:
                effective_items.extend(review_items(store, job, dataset, prefix, errors, warnings, audio_coverage))
            except (ValueError, OSError, ImportError, HTTPException) as error:
                errors.add(f'Dataset "{dataset["name"]}": {error}', dataset_id=entry.dataset_id)
    # Image sections always cache one frame. Clip controls matter only to videos;
    # frames still controls generated samples/previews even when training uses images.
    errors.extend(
        configuration_errors(
            job, has_videos=not effective_items or any(item["kind"] == "video" for item in effective_items)
        )
    )
    output = Path(settings.output_root) / "runs" / f"{job_folder(job.name)}-{{timestamp}}"
    cache = Path(settings.cache_root) / "runs" / f"{job_folder(job.name)}-{{timestamp}}"
    snapshot = output
    add_duplicate_warnings(datasets, effective_items, warnings)
    if job.family == "h3" and any(item["kind"] == "image" for item in effective_items):
        warnings.extend(H3_IMAGE_WARNINGS)
    windows = plan_job_windows(job, datasets, effective_items, errors, warnings)
    sections = plan_sections(job, effective_items, snapshot, cache)
    balance = section_balance(job, sections, windows["items"], {item["id"]: item["name"] for item in datasets})
    if len(balance) > 1:
        warnings.extend(
            f'Balance: "{row["dataset"]}" ({row["kind"]}s) has {row["share"]:.0%} of the training samples per epoch, '
            "so the other sections have little influence. Consider more repeats on the others."
            for row in balance
            if row["share"] > BALANCE_WARNING_SHARE
        )
    disk = disk_preflight(job, settings, effective_items, windows["items"], fixture_mode=fixture_mode)
    warnings.extend(volume["warning"] for volume in disk["volumes"] if volume["warning"])
    stages: list[Stage] = []
    try:
        stages = build_stages(job, settings, snapshot, cache, {item["kind"] for item in effective_items})
        if settings.output_root:
            within_root(output, [Path(settings.output_root)])
        if settings.cache_root:
            within_root(cache, [Path(settings.cache_root)])
    except (ValueError, KeyError) as error:
        errors.append(f"Commands: {error}")
    return {
        "valid": not errors,
        "optimizer": job.config.optimizer,
        "timestep_sampling": job.config.timestep_sampling,
        "timestep_range_policy": timestep_range_policy(job),
        "timestep_parameters": (
            {"sigmoid_scale": job.config.sigmoid_scale, "discrete_flow_shift": job.config.discrete_flow_shift}
            if job.family == "wan" and job.config.timestep_sampling == "shift"
            else {"sigmoid_scale": job.config.sigmoid_scale}
            if job.family == "wan" and job.config.timestep_sampling == "sigmoid"
            else {}
        ),
        "error_targets": errors.targets,
        "warning_targets": warnings.targets,
        "errors": errors,
        "warnings": warnings,
        "job_id": job.id,
        "revision": job.revision,
        "dataset": datasets[0] if datasets else {},
        "datasets": datasets,
        "effective_items": effective_items,
        "effective_count": len(effective_items),
        "audio_coverage": audio_coverage,
        "video_windows": windows,
        "cache_policy": {"latent_cache": job.config.latent_cache_policy, "text_cache": job.config.text_cache_policy},
        "disk": disk,
        "models": {key: value for key, value in settings.models.items() if key.startswith(job.family + "_")},
        "model_provenance": model_provenance,
        "expected_upstream": UPSTREAM,
        "upstream_revision": actual_revision,
        "paths": {"output": str(output), "cache": str(cache), "snapshot": str(snapshot)},
        "sample": {
            "width": sample_geometry(job.config)[0],
            "height": sample_geometry(job.config)[1],
            "frames": sample_geometry(job.config)[2],
            "fps": 16 if job.family == "wan" else 24,
            "duration": sample_geometry(job.config)[2] / (16 if job.family == "wan" else 24),
            "native_preview_frames": native_preview_frames(job.family, job.config),
        },
        "sections": balance,
        "dataset_toml": dataset_toml(job, sections),
        "stages": [stage.record() for stage in stages],
        "expert_schedule": (
            {
                "interval": job.config.expert_interval,
                "counts": (
                    {"low": job.config.steps, "high": job.config.steps}
                    if job.config.wan_steps_per_expert
                    else expert_steps(job.config.steps, job.config.expert_interval)
                ),
                "phases": [
                    phase.record()
                    for phase in (
                        paired_phases(
                            job.config.steps,
                            job.config.expert_interval,
                            save_every=job.config.save_every,
                            preview_every=job.config.preview_every,
                        )
                        if job.config.wan_steps_per_expert
                        else alternating_phases(
                            job.config.steps, job.config.expert_interval, preview_every=job.config.preview_every
                        )
                    )
                ],
            }
            if job.family == "wan"
            and job.config.recipe == PAIRED_RECIPE
            and job.config.expert_interval > 0
            and job.config.steps > 0
            and math.ceil(job.config.steps / job.config.expert_interval) <= MAX_PHASES
            and not any(message.startswith(("Preview interval:", "Expert interval:")) for message in errors)
            and job.config.save_every > 0
            else None
        ),
    }

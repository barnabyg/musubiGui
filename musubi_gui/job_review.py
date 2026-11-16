"""Review: what preparing a Job would do, and the errors and warnings that block or qualify it."""

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
from musubi_gui.job_review_datasets import ReviewMessages, add_duplicate_warnings, plan_job_windows, review_items
from musubi_gui.job_stages import build_stages, effective_settings
from musubi_gui.job_validation import configuration_errors
from musubi_gui.recipe import UPSTREAM, Stage
from musubi_gui.redaction import redact
from musubi_gui.run_paths import job_folder
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.sampling import native_preview_frames, sample_geometry
from musubi_gui.settings import Settings, validate_paths, within_root
from musubi_gui.training_sections import dataset_resolution, dataset_toml, plan_sections, section_balance
from musubi_gui.wan import validate_wan
from musubi_gui.wan_pair import MAX_PHASES, PAIRED_RECIPE, alternating_phases, expert_steps, paired_phases

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

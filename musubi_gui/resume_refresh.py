"""Resuming with a refreshed dataset snapshot: what changed, the preparation it needs, and the segment's own snapshot.

The segment copies the media and captions again into its own folder and gets its own cache folder. Before each cache
stage it copies the source Run's verified cache files of every item whose cache inputs are unchanged, renamed for the
item's new place in the snapshot; the stage then runs with `--skip_existing`, so only the other items are cached. A
stage with nothing left to cache doesn't run at all.
"""

from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from musubi_gui.disk_estimate import TEXT_CACHE_BYTES, check_volumes, free_space_reader, latent_cache_bytes
from musubi_gui.job_models import Job
from musubi_gui.resume_dataset import cache_plan, dataset_changes
from musubi_gui.run_paths import create_run_directory
from musubi_gui.settings import training_settings
from musubi_gui.snapshots import identify_job, job_manifest, materialize_snapshot
from musubi_gui.training_sections import dataset_resolution, plan_sections
from musubi_gui.video_sampling import plan_windows

CACHE_STAGES = ("latent_cache", "text_cache")
# Upstream's H3 latent cacher checks each cache against the media's resolved path, which a new snapshot changes, so
# once it runs it caches every item again. Its caches are copied only when every item can reuse one and it doesn't run.
PATH_BOUND = {"h3": {"latent_cache"}}
UNCOND = "uncond.safetensors"


def _stem(item: dict[str, Any]) -> str | None:
    return Path(item["snapshot_path"]).stem if item.get("snapshot_path") else None


def source_caches(original: dict[str, Any], stage: str) -> dict[str, list[dict[str, str]]]:
    """The source Run's verified cache files for `stage`, by the frozen item they belong to."""
    artifacts: list[dict[str, str]] = next(
        (
            entry.get("artifacts", [])
            for entry in original["stages"]
            if entry["name"] == stage and entry.get("status") in {"completed", "reused"}
        ),
        [],
    )
    stems = {stem: item["id"] for item in original["manifest"]["items"] if (stem := _stem(item))}
    files: dict[str, list[dict[str, str]]] = {}
    for artifact in artifacts:
        owner = stems.get(Path(artifact["path"]).name.split("_", 1)[0])
        if owner:
            files.setdefault(owner, []).append(artifact)
    return files


def seed_plan(original: dict[str, Any], revision: Job, current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """For each cache stage, the current items that copy a frozen item's cache files, and the items to cache."""
    plan = cache_plan(original["job"], original["manifest"], revision, current)
    policies = {"latent_cache": revision.config.latent_cache_policy, "text_cache": revision.config.text_cache_policy}
    result = {}
    for stage in CACHE_STAGES:
        files = source_caches(original, stage) if policies[stage] != "rebuild" else {}
        reuse = {new: old for new, old in plan[stage]["reuse"].items() if files.get(old)}
        if stage in PATH_BOUND.get(revision.family, set()) and len(reuse) < len(current["items"]):
            reuse = {}
        rebuild = [item["id"] for item in current["items"] if item["id"] not in reuse]
        result[stage] = {"reuse": reuse, "rebuild": rebuild, "files": files}
    return result


def _bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _new_cache_bytes(revision: Job, current: dict[str, Any], seeds: dict[str, dict[str, Any]]) -> int:
    """Estimated bytes of the caches the segment builds, from the sizes Run review estimates with."""
    config = revision.config
    latent = set(seeds["latent_cache"]["rebuild"])
    items = [
        {**item, "resolution": dataset_resolution(revision, item.get("dataset_id", ""))} for item in current["items"]
    ]
    images = [item for item in items if item["kind"] == "image" and item["id"] in latent]
    videos = [item for item in items if item["kind"] == "video" and item["id"] in latent]
    windows: list[dict[str, Any]] = []
    for entry in revision.datasets:
        planned = plan_windows(
            revision.family,
            config.frames,
            [item for item in videos if item.get("dataset_id") == entry.dataset_id],
            extraction=config.frame_extraction,
            stride=config.frame_stride,
            sample=config.frame_sample,
        )["items"]
        resolution = dataset_resolution(revision, entry.dataset_id)
        windows.extend({**window, "resolution": resolution} for window in planned)
    text = TEXT_CACHE_BYTES[revision.family] * len(seeds["text_cache"]["rebuild"])
    return latent_cache_bytes(revision.family, config, [*images, *windows]) + text


def preparation(
    original: dict[str, Any],
    revision: Job,
    current: dict[str, Any],
    seeds: dict[str, dict[str, Any]],
    *,
    root: Path,
    fixture_mode: bool,
) -> dict[str, Any]:
    """The caching a refreshed segment does before training, and the disk it needs on each volume."""
    names = {item["id"]: item["name"] for item in current["items"]}
    source = Path(original["cache"])
    copied = sum(
        _bytes(source / file["path"])
        for stage in seeds.values()
        for old in stage["reuse"].values()
        for file in stage["files"][old]
    )
    media = sum(_bytes(Path(item["absolute_path"])) for item in current["items"])
    new = _new_cache_bytes(revision, current, seeds)
    estimate = {
        "media_snapshot": media,
        "copied_caches": copied,
        "new_caches": new,
        "output_root": media,
        "cache_root": copied + new,
    }
    settings = training_settings(root, fixture_mode=fixture_mode)
    return {
        "items": len(current["items"]),
        "stages": [stage for stage in CACHE_STAGES if seeds[stage]["rebuild"]],
        **{
            stage: {"reuse": len(seeds[stage]["reuse"]), "rebuild": [names[key] for key in seeds[stage]["rebuild"]]}
            for stage in CACHE_STAGES
        },
        "estimate": estimate,
        "volumes": check_volumes(settings, estimate, free=free_space_reader(fixture_mode=fixture_mode)),
    }


def inspect_refresh(root: Path, original: dict[str, Any], revision: Job, *, fixture_mode: bool) -> dict[str, Any]:
    """`compatibility`'s dataset arguments: how the datasets differ from `original`'s snapshot and what re-preparing
    them would do, or why their current contents can't be read. Nothing for a frozen Job this version can't read,
    which `compatibility` refuses itself."""
    try:
        Job.model_validate(original["job"])
    except ValidationError:
        return {}
    store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
    try:
        current = job_manifest(store, revision)
    except KeyError as error:
        return {"dataset_error": str(error.args[0]) if error.args else "A dataset of this Job isn't registered."}
    except (ValueError, OSError) as error:
        return {"dataset_error": str(error)}
    if not current["items"]:
        return {"dataset_error": "No effective dataset items remain."}
    seeds = seed_plan(original, revision, current)
    return {
        "dataset": dataset_changes(original["manifest"], current),
        "preparation": preparation(original, revision, current, seeds, root=root, fixture_mode=fixture_mode),
    }


@dataclass
class Refresh:
    """A resumed segment's own dataset snapshot, and the cache files it copies from its source Run."""

    cache: Path
    manifest: dict[str, Any]
    input_files: dict[str, str]
    stage_keys: dict[str, str]
    provenance: dict[str, Any]
    # Stored as the segment's `refresh`, which preparation reads to copy the reusable caches.
    seeds: dict[str, Any]
    dataset: dict[str, Any]

    @property
    def kinds(self) -> set[str]:
        return {item["kind"] for item in self.manifest["items"]}

    def fields(self) -> dict[str, Any]:
        """The segment record's fields that differ from a segment reading its source's snapshot."""
        return {
            "cache": str(self.cache),
            "manifest": self.manifest,
            "input_files": dict(self.input_files),
            "stage_keys": self.stage_keys,
            "provenance": self.provenance,
            "refresh": self.seeds,
            "status": "pending_preparation",
            "phase": "prepare",
            # Resuming queues training after the preparation.
            "start_after_prepare": True,
        }


# Compared with the source Run's, so a resumed segment never caches or trains with other models or code.
_STABLE_PROVENANCE = ("models", "checkout", "cache_environment")


def refreshed_snapshot(
    root: Path, original: dict[str, Any], revision: Job, directory: Path, *, fixture_mode: bool
) -> Refresh:
    """Snapshot `revision`'s datasets into the segment folder `directory`, with a new cache folder beside the
    source's, and plan which of the source's cache files each stage copies."""
    inputs = identify_job(root, revision, fixture_mode=fixture_mode)
    previous = original.get("provenance", {})
    for key in _STABLE_PROVENANCE:
        if key in previous and inputs.provenance.get(key) != previous[key]:
            raise ValueError(
                "A model component or the training environment changed since this Run was prepared, so its saved "
                "state can't resume with a refreshed dataset. Prepare a new Run."
            )
    cache = create_run_directory(Path(original["cache"]).parent, revision.name, "resume")
    try:
        plan = materialize_snapshot(inputs, directory=directory, cache=cache)
    except BaseException:
        # Nothing was cached into it yet; the segment folder keeps the snapshot's own error.
        with suppress(OSError):
            cache.rmdir()
        raise
    manifest = plan["manifest"]
    seeds = seed_plan(original, revision, manifest)
    folders = {
        item["id"]: section.cache
        for section in plan_sections(revision, manifest["items"], directory, cache)
        for item in section.items
    }
    stems = {item["id"]: _stem(item) for item in original["manifest"]["items"]}
    names = {item["id"]: item["name"] for item in manifest["items"]}
    caches: dict[str, Any] = {}
    for stage in CACHE_STAGES:
        copies = []
        for item in manifest["items"]:
            old = seeds[stage]["reuse"].get(item["id"])
            if old is None:
                continue
            stem, new_stem = str(stems[old]), str(_stem(item))
            for file in seeds[stage]["files"][old]:
                name = new_stem + Path(file["path"]).name[len(stem) :]
                target = (folders[item["id"]] / name).relative_to(cache)
                copies.append({"from": file["path"], "to": target.as_posix(), "sha256": file["sha256"]})
        caches[stage] = {"copy": copies, "rebuild": [names[key] for key in seeds[stage]["rebuild"]]}
    uncond = [
        artifact
        for entry in original["stages"]
        if entry["name"] == "text_cache"
        for artifact in entry.get("artifacts", [])
        if artifact["path"] == UNCOND
    ]
    if uncond and caches["text_cache"]["copy"]:
        caches["text_cache"]["copy"].append({"from": UNCOND, "to": UNCOND, "sha256": uncond[0]["sha256"]})
    return Refresh(
        cache=cache,
        manifest=manifest,
        input_files=plan["input_files"],
        stage_keys=plan["stage_keys"],
        provenance=plan["provenance"],
        seeds={"source_run": original["id"], "source_cache": original["cache"], "caches": caches},
        dataset=dataset_changes(original["manifest"], manifest),
    )

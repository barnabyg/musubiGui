"""Copy effective dataset inputs and fingerprint preparation dependencies."""

import hashlib
import json
import shutil
import uuid
from importlib import import_module
from pathlib import Path
from typing import Any

from musubi_gui.diagnostics import training_environment
from musubi_gui.jobs import build_stages, dataset_toml, effective_settings, load_job, review_job
from musubi_gui.reference import safetensors_header
from musubi_gui.settings import atomic_json, load_settings, within_root


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_identity(path: str) -> dict[str, Any]:
    file = Path(path)
    stat = file.stat()
    return {
        "path": str(file.resolve()),
        "bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "header_sha256": digest(safetensors_header(file)) if file.suffix == ".safetensors" else None,
    }


def snapshot_plan(root: Path, job_id: str, *, fixture_mode: bool = False) -> dict[str, Any]:
    job = load_job(root, job_id)
    settings = effective_settings(job, load_settings(root))
    review = review_job(root, job, settings, fixture_mode=fixture_mode)
    if not review["valid"]:
        raise ValueError("Preparation blocked: " + " ".join(review["errors"]))
    if job.family != "wan":
        raise ValueError("Only Wan preparation is available.")
    store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
    manifest: dict[str, Any] = store.manifest(job.dataset_id)
    if not manifest["items"]:
        raise ValueError("No effective dataset items remain.")
    models = {
        key: model_identity(value)
        for key, value in settings.models.items()
        if key.startswith(job.family + "_") and value
    }
    provenance = {
        "upstream_revision": review["upstream_revision"],
        "models": models,
        "environment": {"synthetic_fixture": True} if fixture_mode else training_environment(settings),
    }
    preparation_settings = job.config.model_dump(
        include={"width", "height", "frames", "batch_size", "repeats", "enable_bucket", "bucket_no_upscale", "recipe"}
    )
    media = [{"path": item["path"], "sha256": item["sha256"]} for item in manifest["items"]]
    captions = [
        {"path": item["path"], "text": item["caption"]["text"], "exists": item["caption"]["exists"]}
        for item in manifest["items"]
    ]
    keys = {
        "latent_cache": digest(
            {
                "media": media,
                "settings": preparation_settings,
                "models": {k: v for k, v in models.items() if "vae" in k},
                "upstream": provenance["upstream_revision"],
            }
        ),
        "text_cache": digest(
            {
                "captions": captions,
                "settings": preparation_settings,
                "models": {k: v for k, v in models.items() if k.endswith(("_t5", "_text"))},
                "upstream": provenance["upstream_revision"],
            }
        ),
    }
    identity = digest({"job": job.model_dump(), "media": media, "captions": captions, "provenance": provenance})
    token = uuid.uuid4().hex
    directory = within_root(Path(settings.output_root) / "runs" / token, [Path(settings.output_root)])
    cache = within_root(Path(settings.cache_root) / "runs" / token, [Path(settings.cache_root)])
    directory.mkdir(parents=True)
    media_directory = directory / "media"
    media_directory.mkdir()
    cache.mkdir(parents=True)
    files: dict[str, str] = {}
    try:
        for index, item in enumerate(manifest["items"]):
            source = store.media_path(job.dataset_id, item["id"])
            target = media_directory / f"item{index:06d}{source.suffix.lower()}"
            shutil.copyfile(source, target)
            actual = digest_file(target)
            if actual != item["sha256"]:
                raise ValueError(f"{item['name']} changed during snapshot creation; rescan and retry.")
            item["snapshot_path"] = str(target)
            files[str(target)] = actual
            caption = target.with_suffix(".txt")
            caption.write_text(item["caption"]["text"], encoding="utf-8")
            files[str(caption)] = digest_file(caption)
        native = directory / "dataset.toml"
        native.write_text(dataset_toml(job, media_directory, cache), encoding="utf-8")
        files[str(native)] = digest_file(native)
        atomic_json(directory / "provenance.json", provenance)
        return {
            "job": job.model_dump(),
            "settings": settings,
            "manifest": manifest,
            "directory": directory,
            "cache": cache,
            "stages": build_stages(job, settings, directory, cache),
            "fingerprint": identity,
            "stage_keys": keys,
            "input_files": files,
            "provenance": provenance,
        }
    except BaseException:
        atomic_json(
            directory / "snapshot-error.json",
            {
                "status": "incomplete",
                "message": "Snapshot creation failed; sources were preserved. Remove this incomplete run directory after inspecting it.",
            },
        )
        raise


def verify_inputs(run: dict[str, Any]) -> None:
    for path, expected in run.get("input_files", {}).items():
        if not Path(path).is_file() or digest_file(Path(path)) != expected:
            raise ValueError(
                "Frozen dataset inputs changed or disappeared. Create a new preparation; the original run is retained."
            )
    for identity in run.get("provenance", {}).get("models", {}).values():
        if model_identity(identity["path"]) != identity:
            raise ValueError("A model component changed after preparation. Prepare a new revision.")

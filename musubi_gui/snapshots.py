"""Copy effective dataset inputs and fingerprint preparation dependencies."""

import hashlib
import json
import shutil
import subprocess  # nosec B404
import uuid
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from musubi_gui.diagnostics import training_environment, upstream_revision
from musubi_gui.jobs import Job, JobStore, build_stages, dataset_toml, effective_settings, load_job, review_job
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import Settings, atomic_json, load_settings, within_root
from musubi_gui.video_sampling import WAN_FPS, source_rate, source_rates, video_directory


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


def checkout_identity(checkout: Path) -> dict[str, str]:
    git = shutil.which("git")
    if not git:
        raise ValueError("Git is required to validate the prepared training source.")
    # B603: fixed read-only source enumeration; no shell or user command arguments.
    result = subprocess.run(
        [git, "-C", str(checkout), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )  # nosec B603
    files = {}
    for name in result.stdout.split("\0"):
        if name and (name.endswith(".py") or name in {"pyproject.toml", "uv.lock"}):
            path = within_root(checkout / name, [checkout])
            if not path.is_file():
                raise ValueError("Training source disappeared during preparation. Retry after the checkout is stable.")
            files[name] = digest_file(path)
    return {"revision": upstream_revision(checkout), "source_digest": digest(files)}


def cache_environment_identity(settings: Settings, environment: dict[str, Any]) -> dict[str, Any]:
    """Only stable producer identities belong in reusable-cache keys."""
    executable = Path(settings.python).resolve()
    stat = executable.stat()
    return {
        "executable": {"path": str(executable), "bytes": stat.st_size, "modified_ns": stat.st_mtime_ns},
        "python": environment.get("python"),
        "platform": environment.get("platform"),
        "packages": environment.get("packages", {}),
        "preprocessing_packages": environment.get("preprocessing_packages", {}),
        "cuda": environment.get("cuda"),
    }


@dataclass
class SnapshotIdentity:
    root: Path
    job: Job
    settings: Settings
    manifest: dict[str, Any]
    provenance: dict[str, Any]
    stage_keys: dict[str, str]
    fingerprint: str


def identify_snapshot(root: Path, job_id: str, *, fixture_mode: bool = False) -> SnapshotIdentity:
    job = load_job(root, job_id)
    if JobStore(root).is_archived(job_id):
        raise ValueError("This job is archived. Restore it from Jobs before preparing a new Run.")
    settings = effective_settings(job, load_settings(root))
    review = review_job(root, job, settings, fixture_mode=fixture_mode)
    if not review["valid"]:
        raise ValueError("Preparation blocked: " + " ".join(review["errors"]))
    store: Any = import_module("musubi_gui.datasets").DatasetStore(root)
    manifest: dict[str, Any] = store.manifest(job.dataset_id)
    if not manifest["items"]:
        raise ValueError("No effective dataset items remain.")
    models = {
        key: model_identity(value)
        for key, value in settings.models.items()
        if key.startswith(job.family + "_") and value
    }
    environment = training_environment(settings)
    if fixture_mode:
        environment["synthetic_fixture"] = True
    provenance = {
        "upstream_revision": review["upstream_revision"],
        "models": models,
        "environment": environment,
        "checkout": checkout_identity(Path(settings.checkout)),
        "cache_environment": cache_environment_identity(settings, environment),
        "audio_coverage": review.get("audio_coverage", {}),
    }
    preparation_settings = job.config.model_dump(
        include={
            "width",
            "height",
            "frames",
            "batch_size",
            "repeats",
            "enable_bucket",
            "bucket_no_upscale",
            "recipe",
            "audio_policy",
        }
    )
    if job.family == "wan":
        # Sections resample each source rate to 16 fps; caches built from un-resampled TOML are never reused.
        config = job.config
        preparation_settings["video_sampling"] = {
            "target_fps": WAN_FPS,
            "source_fps": source_rates(manifest["items"]),
            "frame_extraction": config.frame_extraction,
            "frame_stride": config.frame_stride if config.frame_extraction == "slide" else None,
            "frame_sample": config.frame_sample if config.frame_extraction == "uniform" else None,
        }
    media = [
        {"path": item["path"], "sha256": item["sha256"], "audio": item.get("audio") if job.family == "h3" else None}
        for item in manifest["items"]
    ]
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
                "checkout": provenance["checkout"],
                "environment": provenance["cache_environment"],
            }
        ),
        "text_cache": digest(
            {
                "captions": captions,
                "settings": preparation_settings,
                "models": {k: v for k, v in models.items() if k.endswith(("_t5", "_text"))},
                "checkout": provenance["checkout"],
                "environment": provenance["cache_environment"],
            }
        ),
    }
    stable_provenance: dict[str, Any] = {**provenance}
    stable_provenance["environment"] = provenance["cache_environment"]
    identity = digest(
        {
            "job": job.model_dump(),
            "settings": settings.model_dump(),
            "media": media,
            "captions": captions,
            "provenance": stable_provenance,
        }
    )
    return SnapshotIdentity(root, job, settings, manifest, provenance, keys, identity)


def materialize_snapshot(inputs: SnapshotIdentity) -> dict[str, Any]:
    job, settings, manifest = inputs.job, inputs.settings, inputs.manifest
    provenance, keys, identity = inputs.provenance, inputs.stage_keys, inputs.fingerprint
    store: Any = import_module("musubi_gui.datasets").DatasetStore(inputs.root)
    token = uuid.uuid4().hex
    directory = within_root(Path(settings.output_root) / "runs" / token, [Path(settings.output_root)])
    cache = within_root(Path(settings.cache_root) / "runs" / token, [Path(settings.cache_root)])
    directory.mkdir(parents=True)
    snapshot_record: dict[str, Any] = {
        "version": 1,
        "status": "materializing",
        "fingerprint": identity,
        "job": job.model_dump(),
        "settings": settings.model_dump(),
        "manifest": manifest,
        "provenance": provenance,
        "cache": str(cache),
        "notice": "A snapshot without a published Run is incomplete; source files are preserved.",
    }
    atomic_json(directory / "snapshot.json", snapshot_record)
    files: dict[str, str] = {}
    try:
        atomic_json(directory / "provenance.json", provenance)
        media_directory = directory / "media"
        media_directory.mkdir()
        cache.mkdir(parents=True)
        rates = source_rates(manifest["items"]) if job.family == "wan" else []
        for index, item in enumerate(manifest["items"]):
            source = store.media_path(job.dataset_id, item["id"])
            folder = media_directory
            if rates and item["kind"] == "video":
                folder = video_directory(media_directory, rates, source_rate(item))
                folder.mkdir(exist_ok=True)
            target = folder / f"item{index:06d}{source.suffix.lower()}"
            shutil.copyfile(source, target)
            actual = digest_file(target)
            if actual != item["sha256"]:
                raise ValueError(f"{item['name']} changed during snapshot creation; rescan and retry.")
            item["snapshot_path"] = str(target)
            files[str(target)] = actual
            caption = target.with_suffix(".txt")
            caption.write_text(item["caption"]["text"], encoding="utf-8")
            files[str(caption)] = digest_file(caption)
            audio = item.get("audio") if job.family == "h3" else None
            if audio and audio.get("source") in {"explicit", "sidecar"}:
                source_audio = Path(audio["audio_path"])
                audio_directory = directory / "audio"
                audio_directory.mkdir(exist_ok=True)
                target_audio = audio_directory / (target.stem + source_audio.suffix.lower())
                shutil.copyfile(source_audio, target_audio)
                actual_audio = digest_file(target_audio)
                if actual_audio != audio["audio_sha256"]:
                    raise ValueError(f"{item['name']}: audio changed while freezing the dataset. Rescan and retry.")
                audio["snapshot_path"] = str(target_audio)
                files[str(target_audio)] = actual_audio
        native = directory / "dataset.toml"
        native_text = dataset_toml(job, media_directory, cache, video_rates=rates)
        if job.family == "h3":
            records = []
            for item in manifest["items"]:
                record = {"video_path": item["snapshot_path"], "caption": item["caption"]["text"]}
                audio = item.get("audio")
                if audio and audio.get("source") in {"explicit", "sidecar"}:
                    record["audio_path"] = audio["snapshot_path"]
                records.append(record)
            metadata = directory / "dataset.jsonl"
            metadata.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8"
            )
            files[str(metadata)] = digest_file(metadata)
            native_text = native_text.replace(
                "video_directory = " + json.dumps(media_directory.as_posix(), ensure_ascii=False),
                "video_jsonl_file = " + json.dumps(metadata.as_posix(), ensure_ascii=False),
            )
        native.write_text(native_text, encoding="utf-8")
        files[str(native)] = digest_file(native)
        snapshot_record.update(status="materialized", input_files=files)
        atomic_json(directory / "snapshot.json", snapshot_record)
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
        snapshot_record.update(status="incomplete")
        atomic_json(directory / "snapshot.json", snapshot_record)
        atomic_json(
            directory / "snapshot-error.json",
            {
                "status": "incomplete",
                "message": "Snapshot creation failed; sources were preserved. Remove this incomplete run directory after inspecting it.",
            },
        )
        raise


def snapshot_plan(root: Path, job_id: str, *, fixture_mode: bool = False) -> dict[str, Any]:
    return materialize_snapshot(identify_snapshot(root, job_id, fixture_mode=fixture_mode))


def verify_inputs(run: dict[str, Any]) -> None:
    for path, expected in run.get("input_files", {}).items():
        if not Path(path).is_file() or digest_file(Path(path)) != expected:
            raise ValueError(
                "Frozen dataset inputs changed or disappeared. Create a new preparation; the original run is retained."
            )
    for identity in run.get("provenance", {}).get("models", {}).values():
        if model_identity(identity["path"]) != identity:
            raise ValueError("A model component changed after preparation. Prepare a new revision.")
    previous = run.get("provenance", {}).get("checkout")
    if previous and checkout_identity(Path(run["settings"]["checkout"])) != previous:
        raise ValueError(
            "The training checkout changed after preparation. Prepare a new revision to record its actual code."
        )
    expected_runtime = run.get("provenance", {}).get("cache_environment")
    if expected_runtime:
        settings = Settings.model_validate(run["settings"])
        current_runtime = cache_environment_identity(settings, training_environment(settings, versions_only=True))
        for key in ["executable", "python", "platform", "packages", "preprocessing_packages"]:
            # Earlier records did not record optional preprocessors. Preserve their readability.
            if key in expected_runtime and current_runtime[key] != expected_runtime[key]:
                raise ValueError(
                    "The selected training environment changed after preparation. Prepare a new revision to record its actual package versions."
                )

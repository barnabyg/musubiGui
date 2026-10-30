"""Share the UMT5 tokenizer the pinned Wan code loads by repository name, so later Runs need no Hub access."""

import hashlib
import json
import os
import shutil
from pathlib import Path

REPOSITORY = "models--Wan-AI--Wan2.1-T2V-14B"
COMMIT = "a064a6c71f5be440641209c07bf2a5ce7a2ff5e4"  # pragma: allowlist secret
SUBFOLDER = "google/umt5-xxl"
# Downloaded by run fb66e3d3 in #26. A different download stays in its own Run and is not shared.
FILES = {
    "special_tokens_map.json": "7b8a9f5040adb67b5805abdfd42c1f8d0f3d0e711f10726580eb3789cd0ad61d",  # pragma: allowlist secret
    "spiece.model": "e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458",  # pragma: allowlist secret
    "tokenizer.json": "6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b",  # pragma: allowlist secret
    "tokenizer_config.json": "ed9a3a8b0faa71a70a32847e0435fe036e6e112d4df4edb7bb48a921e344dc05",  # pragma: allowlist secret
}


def shared_directory(cache_root: str) -> Path:
    return Path(cache_root) / "shared" / "wan-umt5-tokenizer"


def _verified(folder: Path) -> bool:
    try:
        return all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == file for name, file in FILES.items())
    except OSError:
        return False


def _snapshot(run_cache: Path) -> Path:
    return run_cache / "huggingface" / "hub" / REPOSITORY / "snapshots" / COMMIT / SUBFOLDER


def publish_shared(run_cache: Path, shared: Path) -> bool:
    """Keep a verified download from this Run's cache for later Runs."""
    source = _snapshot(run_cache)
    if not _verified(source):
        return False
    if _verified(shared):
        return True
    shared.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        pending = shared / f".pending-{name}"
        shutil.copyfile(source / name, pending)
        os.replace(pending, shared / name)
    return _verified(shared)


def seed_run(shared: Path, run_cache: Path) -> bool:
    """Lay out the shared copy as a Hub cache entry; False leaves the Run to download it as before."""
    if not _verified(shared):
        return False
    folder = _snapshot(run_cache)
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(shared / name, folder / name)
    # The repository has no config here. Online, that 404 gives an empty config; offline, transformers
    # treats the same missing file as a connection error, even with every tokenizer file cached.
    (folder / "config.json").write_text(json.dumps({}), encoding="utf-8")
    refs = run_cache / "huggingface" / "hub" / REPOSITORY / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(COMMIT, encoding="utf-8")
    return True

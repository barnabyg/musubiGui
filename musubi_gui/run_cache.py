"""Preparation caches: verified artifacts, reuse from compatible Runs and the shared Wan tokenizer."""

import shutil
from pathlib import Path
from typing import Any

from musubi_gui.run_store import RunStore
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_tokenizer import publish_shared, seed_run, shared_directory


def cache_artifacts(run: dict[str, Any], stage: str) -> list[dict[str, str]]:
    cache = Path(run["cache"])
    # dataset, dataset-images and dataset-rate-N, with dataset-N in place of dataset for several datasets.
    candidates = list(cache.glob("dataset*/*.safetensors"))
    files = [file for file in candidates if file.name.endswith("_te.safetensors") == (stage == "text_cache")]
    if run["job"]["family"] == "h3" and stage == "text_cache":
        uncond = cache / "uncond.safetensors"
        if not uncond.is_file():
            raise ValueError("H3 text caching did not produce the unconditional guidance cache.")
        files.append(uncond)
    for item in run["manifest"]["items"]:
        if "snapshot_path" in item and not any(
            file.name.startswith(Path(item["snapshot_path"]).stem + "_") for file in files
        ):
            raise ValueError(
                f"{item['name']}: {stage} did not prepare this included item. Inspect the stage log before retrying."
            )
    if any(file.stat().st_size == 0 for file in files):
        raise ValueError(f"{stage} contains empty/incomplete cache files.")
    return [{"path": str(file.relative_to(cache)), "sha256": digest_file(file)} for file in files]


def reuse_cache(store: RunStore, run: dict[str, Any], stage_name: str, index: int) -> bool:
    """Copy a compatible Run's verified artifacts for this stage; returns whether the stage was reused."""
    policy = "latent_cache_policy" if stage_name == "latent_cache" else "text_cache_policy"
    rebuild = run["job"].get("config", {}).get(policy, "reuse") == "rebuild"
    reason = "Rebuild requested by this Run." if rebuild else "No verified compatible cache found."
    store.update(run["id"], lambda r: r["stages"][index].update(cache_reason=reason))
    if rebuild:
        return False
    key = run.get("stage_keys", {}).get(stage_name)
    if not key:
        return False
    for previous in store.list():
        if previous["id"] == run["id"] or previous.get("stage_keys", {}).get(stage_name) != key:
            continue
        old: dict[str, Any] = next((stage for stage in previous["stages"] if stage["name"] == stage_name), {})
        artifacts = old.get("artifacts", [])
        if not artifacts or old.get("status") not in {"completed", "reused"} or previous["status"] == "deleting":
            continue
        try:
            if not all(
                (Path(previous["cache"]) / file["path"]).is_file()
                and digest_file(Path(previous["cache"]) / file["path"]) == file["sha256"]
                for file in artifacts
            ):
                continue
            for file in artifacts:
                target = Path(run["cache"]) / file["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(previous["cache"]) / file["path"], target)
                # Copies keep their build time, which shows that nothing cached them again.
                shutil.copystat(Path(previous["cache"]) / file["path"], target)
        except (FileNotFoundError, PermissionError):
            # That Run was deleted meanwhile; its cache simply isn't available to reuse.
            continue
        store.update(
            run["id"],
            lambda r: r["stages"][index].update(
                status="reused",
                artifacts=artifacts,
                reused_from=previous["id"],
                cache_reason="Compatible identity and artifact hashes verified.",
                exit_code=0,
            ),
        )
        return True
    return False


def seed_refreshed(store: RunStore, run: dict[str, Any], stage_name: str, index: int) -> bool:
    """Copy a refreshed segment's reusable cache files from its source Run; returns whether nothing is left to cache.

    The copies keep their source's modification time. A file that changed or went missing since resuming is left
    out, so the stage caches that item itself.
    """
    refresh = run.get("refresh")
    plan = (refresh or {}).get("caches", {}).get(stage_name)
    if not refresh or not plan or not plan["copy"]:
        return False
    source, cache = Path(refresh["source_cache"]), Path(run["cache"])
    copied = 0
    for entry in plan["copy"]:
        target = cache / entry["to"]
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source / entry["from"], target)
        except OSError:
            continue
        if digest_file(target) != entry["sha256"]:
            target.unlink()
            continue
        copied += 1
    source_name = refresh["source_run"][:8]
    if copied == len(plan["copy"]) and not plan["rebuild"]:
        artifacts = cache_artifacts(run, stage_name)
        store.update(
            run["id"],
            lambda r: r["stages"][index].update(
                status="reused",
                artifacts=artifacts,
                reused_from=refresh["source_run"],
                cache_reason=f"Every item's inputs are unchanged; copied its cache files from Run {source_name}.",
                exit_code=0,
            ),
        )
        return True
    missing = len(plan["copy"]) - copied
    reason = f"Copied {copied} cache files of unchanged items from Run {source_name}"
    reason += f"; {missing} changed or went missing there and are cached again." if missing else "."
    if plan["rebuild"]:
        reason += f" Caching {len(plan['rebuild'])} changed items: {', '.join(plan['rebuild'][:5])}"
        reason += "…" if len(plan["rebuild"]) > 5 else "."
    store.update(run["id"], lambda r: r["stages"][index].update(cache_reason=reason))
    return False


def wan_tokenizer_directory(run: dict[str, Any]) -> Path | None:
    cache_root = run["settings"].get("cache_root")
    return shared_directory(cache_root) if run["job"]["family"] == "wan" and cache_root else None


def seed_wan_tokenizer(run: dict[str, Any]) -> bool:
    shared = wan_tokenizer_directory(run)
    if not shared:
        return False
    try:
        return seed_run(shared, Path(run["cache"]))
    except OSError:
        # The stage can still download the tokenizer itself.
        return False


def share_wan_tokenizer(run: dict[str, Any]) -> None:
    shared = wan_tokenizer_directory(run)
    if not shared:
        return
    try:
        publish_shared(Path(run["cache"]), shared)
    except OSError:
        # Sharing only saves a later download; it never fails a stage that succeeded.
        return

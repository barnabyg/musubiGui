"""What changed in a Job's datasets since a Run froze them, and which item caches a refreshed segment rebuilds.

A Run freezes copies of its media and captions. Resuming with a refreshed dataset takes a new snapshot, then caches
again only the items whose cache inputs changed: an item's latent cache follows its media and the geometry it is
cached at, and its text cache follows its caption (and, for H3, its media too). Every other item reuses the cache of
an earlier item with the same inputs.
"""

from typing import Any

from musubi_gui.job_models import Job
from musubi_gui.snapshots import digest
from musubi_gui.training_sections import dataset_resolution

# Job settings every latent cache depends on, besides each dataset's own resolution.
_LATENT_SETTINGS = {
    "width",
    "height",
    "frames",
    "frame_extraction",
    "frame_stride",
    "frame_sample",
    "enable_bucket",
    "bucket_no_upscale",
    "recipe",
    "audio_policy",
}


def _items(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # Read leniently: Compare also counts the differences between Runs recorded by earlier versions.
    return {str(item.get("id", item.get("path"))): item for item in manifest.get("items", [])}


def _name(item: dict[str, Any]) -> str:
    return str(item.get("name", item.get("path", item.get("id"))))


def _audio(item: dict[str, Any]) -> dict[str, Any] | None:
    # The frozen copy's location isn't part of what the item is.
    audio = item.get("audio")
    return {key: value for key, value in audio.items() if key != "snapshot_path"} if audio else None


def _caption(item: dict[str, Any]) -> tuple[str, bool]:
    caption = item.get("caption") or {}
    return str(caption.get("text", "")), bool(caption.get("exists", True))


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def dataset_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Caption and media differences between a frozen manifest and the datasets' current contents, by item name."""
    old, new = _items(before), _items(after)
    captions = [_name(item) for key, item in new.items() if key in old and _caption(old[key]) != _caption(item)]
    added = [_name(item) for key, item in new.items() if key not in old]
    removed = [_name(item) for key, item in old.items() if key not in new]
    replaced = [
        _name(item)
        for key, item in new.items()
        if key in old
        and (old[key].get("sha256"), old[key].get("kind"), _audio(old[key]))
        != (item.get("sha256"), item.get("kind"), _audio(item))
    ]
    summary = [
        text
        for count, text in [
            (len(captions), _plural(len(captions), "caption changed", "captions changed")),
            (len(added), f"{len(added)} media added"),
            (len(removed), f"{len(removed)} media removed"),
            (len(replaced), f"{len(replaced)} media replaced"),
        ]
        if count
    ]
    return {
        "changed": bool(summary),
        "captions": captions,
        "added": added,
        "removed": removed,
        "replaced": replaced,
        "summary": summary,
    }


def latent_identity(job: Job, item: dict[str, Any]) -> str:
    """What an item's latent cache depends on in this Job, apart from the models and code verified unchanged."""
    return digest(
        {
            "media": item["sha256"],
            "kind": item.get("kind"),
            "audio": _audio(item) if job.family == "h3" else None,
            "resolution": list(dataset_resolution(job, item.get("dataset_id", ""))),
            "settings": job.config.model_dump(include=_LATENT_SETTINGS),
        }
    )


def text_identity(job: Job, item: dict[str, Any]) -> str:
    """What an item's text cache depends on: its caption, and for H3 the media it presents beside it."""
    return digest(
        {"caption": list(_caption(item)), "media": latent_identity(job, item) if job.family == "h3" else None}
    )


def _stage_plan(
    identity: Any, frozen: Job, before: list[dict[str, Any]], job: Job, after: list[dict[str, Any]]
) -> dict[str, Any]:
    known: dict[str, str] = {}
    for item in before:
        known.setdefault(identity(frozen, item), item["id"])
    old = {item["id"]: identity(frozen, item) for item in before}
    reuse: dict[str, str] = {}
    rebuild: list[str] = []
    for item in after:
        current = identity(job, item)
        # The same item when it is unchanged, otherwise any frozen item with the same inputs.
        source = item["id"] if old.get(item["id"]) == current else known.get(current)
        if source is None:
            rebuild.append(item["id"])
        else:
            reuse[item["id"]] = source
    return {"reuse": reuse, "rebuild": rebuild}


def cache_plan(
    frozen_job: dict[str, Any], frozen: dict[str, Any], job: Job, current: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """For each cache stage, the current items that reuse a frozen item's cache (new id to old id), and the rest."""
    before = Job.model_validate(frozen_job)
    old, new = frozen.get("items", []), current.get("items", [])
    return {
        "latent_cache": _stage_plan(latent_identity, before, old, job, new),
        "text_cache": _stage_plan(text_identity, before, old, job, new),
    }

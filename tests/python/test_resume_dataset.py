"""What changed in a Job's datasets since a Run froze them, and which item caches a refreshed segment rebuilds."""

import copy
from typing import Any

from musubi_gui.job_models import Job, JobConfig, JobDataset
from musubi_gui.resume_dataset import cache_plan, dataset_changes


def item(name: str, *, dataset: str = "data", caption: str = "A cube.", sha: str = "", kind: str = "image") -> dict:
    return {
        "id": f"{dataset}-{name}",
        "dataset_id": dataset,
        "path": name,
        "name": name,
        "kind": kind,
        "sha256": sha or f"sha-{name}",
        "caption": {"text": caption, "revision": f"rev-{caption}", "exists": True},
        "audio": None,
        "metadata": {"width": 256, "height": 256},
    }


def manifest(*items: dict[str, Any]) -> dict[str, Any]:
    return {"dataset": {"id": "data", "name": "Data"}, "items": list(items), "excluded": [], "invalid": []}


def frozen_manifest(*items: dict[str, Any]) -> dict[str, Any]:
    """As a Run records it: each item knows where its snapshot copy is."""
    result = manifest(*copy.deepcopy(items))
    for index, entry in enumerate(result["items"]):
        entry["snapshot_path"] = f"C:/runs/one/media/item{index:06d}.png"
    return result


def job(family: str = "wan", datasets: list[JobDataset] | None = None, **config: Any) -> Job:
    return Job(
        id="job",
        revision=2,
        name="Job",
        family=family,  # type: ignore[arg-type]
        datasets=datasets or [JobDataset(dataset_id="data")],
        config=JobConfig(steps=300, save_state=True, **config),
    )


ONE, TWO, THREE = item("one.png"), item("two.png"), item("three.png")


def test_unchanged_contents_have_no_differences() -> None:
    changes = dataset_changes(frozen_manifest(ONE, TWO), manifest(ONE, TWO))
    assert changes == {"changed": False, "captions": [], "added": [], "removed": [], "replaced": [], "summary": []}


def test_caption_and_media_edits_are_counted_by_kind() -> None:
    edited = [item("one.png", caption="A red cube."), item("two.png", caption="A blue cube.")]
    replaced = item("three.png", sha="new-pixels")
    changes = dataset_changes(
        frozen_manifest(ONE, TWO, THREE, item("gone.png")), manifest(*edited, replaced, item("four.png"))
    )
    assert changes["changed"]
    assert changes["captions"] == ["one.png", "two.png"]
    assert (changes["added"], changes["removed"], changes["replaced"]) == (["four.png"], ["gone.png"], ["three.png"])
    assert changes["summary"] == ["2 captions changed", "1 media added", "1 media removed", "1 media replaced"]


def test_a_caption_file_added_beside_media_counts_as_a_caption_change() -> None:
    missing = item("one.png", caption="")
    missing["caption"]["exists"] = False
    changes = dataset_changes(frozen_manifest(missing), manifest(item("one.png", caption="")))
    assert changes["captions"] == ["one.png"] and changes["summary"] == ["1 caption changed"]


def test_rescanned_metadata_alone_is_no_change() -> None:
    rescanned = {**ONE, "metadata": {"width": 256, "height": 256, "probe": 2}}
    assert not dataset_changes(frozen_manifest(ONE), manifest(rescanned))["changed"]


def test_an_edited_caption_rebuilds_only_that_items_text() -> None:
    plan = cache_plan(
        job().model_dump(), frozen_manifest(ONE, TWO), job(), manifest(item("one.png", caption="A red cube."), TWO)
    )
    assert plan["latent_cache"] == {"reuse": {ONE["id"]: ONE["id"], TWO["id"]: TWO["id"]}, "rebuild": []}
    assert plan["text_cache"] == {"reuse": {TWO["id"]: TWO["id"]}, "rebuild": [ONE["id"]]}


def test_added_and_replaced_media_rebuild_both_caches_but_wan_text_follows_the_caption_only() -> None:
    replaced = item("two.png", sha="new-pixels")
    added = item("four.png", caption="A fourth cube.")
    # A new item whose caption another item already had reuses that text cache.
    same_caption = item("five.png")
    plan = cache_plan(
        job().model_dump(), frozen_manifest(ONE, TWO), job(), manifest(ONE, replaced, added, same_caption)
    )
    assert plan["latent_cache"]["rebuild"] == [TWO["id"], added["id"], same_caption["id"]]
    assert plan["text_cache"]["rebuild"] == [added["id"]]
    assert plan["text_cache"]["reuse"] == {ONE["id"]: ONE["id"], TWO["id"]: TWO["id"], same_caption["id"]: ONE["id"]}


def test_h3_text_caches_read_the_media_too() -> None:
    video = item("clip.mp4", kind="video")
    replaced = item("clip.mp4", kind="video", sha="new-frames")
    plan = cache_plan(job("h3").model_dump(), frozen_manifest(video), job("h3"), manifest(replaced))
    assert plan["latent_cache"]["rebuild"] == plan["text_cache"]["rebuild"] == [video["id"]]


def test_h3_audio_changes_rebuild_latents() -> None:
    video = {**item("clip.mp4", kind="video"), "audio": {"source": "sidecar", "audio_sha256": "a"}}
    frozen = frozen_manifest(video)
    frozen["items"][0]["audio"]["snapshot_path"] = "C:/runs/one/audio/item000000.wav"
    same = cache_plan(job("h3").model_dump(), frozen, job("h3"), manifest(video))
    assert same["latent_cache"]["rebuild"] == []
    louder = {**video, "audio": {"source": "sidecar", "audio_sha256": "b"}}
    assert cache_plan(job("h3").model_dump(), frozen, job("h3"), manifest(louder))["latent_cache"]["rebuild"] == [
        video["id"]
    ]


def test_a_changed_dataset_resolution_rebuilds_only_that_datasets_latents() -> None:
    other = item("other.png", dataset="more")
    before = job(datasets=[JobDataset(dataset_id="data"), JobDataset(dataset_id="more")])
    after = job(datasets=[JobDataset(dataset_id="data"), JobDataset(dataset_id="more", resolution=(320, 320))])
    plan = cache_plan(before.model_dump(), frozen_manifest(ONE, other), after, manifest(ONE, other))
    assert plan["latent_cache"]["rebuild"] == [other["id"]]
    assert plan["text_cache"]["rebuild"] == []


def test_changed_repeats_rebuild_nothing() -> None:
    after = job(datasets=[JobDataset(dataset_id="data", repeats=4)], repeats=2)
    plan = cache_plan(job().model_dump(), frozen_manifest(ONE), after, manifest(ONE))
    assert plan["latent_cache"]["rebuild"] == plan["text_cache"]["rebuild"] == []


def test_a_changed_cache_setting_rebuilds_every_latent() -> None:
    plan = cache_plan(job().model_dump(), frozen_manifest(ONE, TWO), job(width=320), manifest(ONE, TWO))
    assert plan["latent_cache"]["rebuild"] == [ONE["id"], TWO["id"]]
    assert plan["text_cache"]["rebuild"] == []


def test_a_moved_item_reuses_the_cache_of_identical_content_under_its_old_identity() -> None:
    # Renamed in the dataset folder: a new item id, the same pixels and caption.
    moved = {**ONE, "id": "data-renamed.png", "path": "renamed.png", "name": "renamed.png"}
    plan = cache_plan(job().model_dump(), frozen_manifest(ONE), job(), manifest(moved))
    assert plan["latent_cache"] == {"reuse": {moved["id"]: ONE["id"]}, "rebuild": []}
    assert plan["text_cache"] == {"reuse": {moved["id"]: ONE["id"]}, "rebuild": []}

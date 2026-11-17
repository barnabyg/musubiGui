"""Resuming a saved state with a refreshed dataset snapshot: edit a caption or add media, then resume."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from helpers import instant_steps, wait_for
from PIL import Image
from test_run_control import handled, job_with, training
from test_run_resume_revision import edit, unchanged_fields
from test_wan_pair_runs import paired_job

from musubi_gui.datasets import DatasetStore
from musubi_gui.job_store import JobStore
from musubi_gui.outputs import compare_runs
from musubi_gui.runs import RunService
from musubi_gui.wan_pair_plan import argument

NEW_CAPTION = "A red square on a blue background, now captioned again. No sound."


def add_stills(root: Path, job_id: str, *colours: str) -> Path:
    """Add a captioned still per colour to the Job's dataset folder and rescan; returns the folder."""
    store = DatasetStore(root)
    dataset_id = JobStore(root).get(job_id).datasets[0].dataset_id
    folder = Path(store.get(dataset_id)["path"])
    for name, colour in colours_by_name(colours).items():
        Image.new("RGB", (256, 256), colour).save(folder / f"{name}.png")
        (folder / f"{name}.txt").write_text(f"A {colour} still. No sound.", encoding="utf-8")
    store.scan(dataset_id)
    return folder


def colours_by_name(colours: tuple[str, ...]) -> dict[str, str]:
    return {colour.split(":")[0]: colour.split(":")[-1] for colour in colours}


def item_caches(run: dict[str, Any]) -> dict[tuple[str, str], tuple[int, str]]:
    """Each cache file of `run` by the item it belongs to and its name after the item's stem."""
    stems = {Path(item["snapshot_path"]).stem: item["id"] for item in run["manifest"]["items"]}
    cache = Path(run["cache"])
    result = {}
    for file in cache.glob("dataset*/*.safetensors"):
        stem, rest = file.name.split("_", 1)
        result[(stems[stem], rest)] = (file.stat().st_mtime_ns, hashlib.sha256(file.read_bytes()).hexdigest())
    return result


def item_id(run: dict[str, Any], name: str) -> str:
    return next(item["id"] for item in run["manifest"]["items"] if item["name"] == name)


def stage(run: dict[str, Any], name: str) -> dict[str, Any]:
    return next(entry for entry in run["stages"] if entry["name"] == name)


def stage_log(run: dict[str, Any], name: str) -> str:
    return (Path(run["directory"]) / f"{name}.log").read_text(encoding="utf-8", errors="replace")


def stopped_with_state(service: RunService, job_id: str) -> tuple[dict[str, Any], int]:
    """Train the Job until Save and stop leaves a saved state; returns the stopped Run and its step."""
    run = service.prepare(job_id, start=True)
    training(service, run["id"])
    service.control(run["id"], "save_and_stop")
    step = int(handled(service, run["id"], 1)["step"])
    stopped = wait_for(service, run["id"], "stopped", device_released=True)
    service.saved_states(run["id"])
    return stopped, step


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_editing_one_caption_recaches_only_that_items_text_and_continues_from_the_saved_step(
    tmp_path: Path, family: str
) -> None:
    root, job_id = job_with(tmp_path, family, steps=40, save_every=40, save_state=True, sample_enabled=False)
    folder = add_stills(root, job_id, "still0:red", "still1:green")
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        stopped, step = stopped_with_state(service, job_id)
        source = unchanged_fields(service.get(stopped["id"]))
        source_caches = item_caches(stopped)
        assert len(source_caches) == 6

        instant_steps(root)
        (folder / "still0.txt").write_text(NEW_CAPTION, encoding="utf-8")
        check = service.resume_check(stopped["id"])
        assert check["compatible"] and check["reprepare"], check
        assert check["changes"] == []
        assert check["dataset_changes"]["summary"] == ["1 caption changed"]
        assert check["dataset_changes"]["captions"] == ["still0.png"]
        preparation = check["preparation"]
        assert preparation["text_cache"] == {"reuse": 2, "rebuild": ["still0.png"]}
        assert preparation["latent_cache"] == {"reuse": 3, "rebuild": []}
        assert preparation["stages"] == ["text_cache"]
        estimate = preparation["estimate"]
        assert estimate["media_snapshot"] > 0 and estimate["new_caches"] > 0
        assert estimate["cache_root"] == estimate["copied_caches"] + estimate["new_caches"]
        assert all(volume["needed"] > 0 for volume in preparation["volumes"])

        resumed = service.resume(stopped["id"], check["revision"])
        assert resumed["status"] == "pending_preparation"
        completed = wait_for(service, resumed["id"], "completed", device_released=True)

        # Training continued from the saved step.
        assert completed["segment"]["step_offset"] == step
        assert completed["segment"]["dataset_changes"]["summary"] == ["1 caption changed"]
        progress = service.progress(completed["id"])
        assert progress and (progress["step"], progress["total"]) == (40, 40)
        assert f"--resume {stopped['directory']}" in " ".join(stage(completed, "train")["args"])

        # The segment records its own snapshot, with the new caption, and its own cache folder.
        directory = Path(completed["directory"])
        assert (directory / "snapshot.json").is_file()
        assert argument(stage(completed, "train")["args"], "--dataset_config") == str(directory / "dataset.toml")
        edited = next(item for item in completed["manifest"]["items"] if item["name"] == "still0.png")
        assert edited["caption"]["text"] == NEW_CAPTION
        assert Path(edited["snapshot_path"]).with_suffix(".txt").read_text(encoding="utf-8") == NEW_CAPTION
        assert all(Path(path).is_relative_to(directory) for path in completed["input_files"])
        assert completed["cache"] != stopped["cache"]

        # Only that item's text was cached again; every other cache file is the source's, untouched.
        caches = item_caches(completed)
        assert caches.keys() == source_caches.keys()
        changed = (item_id(completed, "still0.png"), f"{'mmh3' if family == 'h3' else 'wan'}_te.safetensors")
        assert caches[changed][0] != source_caches[changed][0]
        for key, value in caches.items():
            if key != changed:
                assert value == source_caches[key], key
        assert "cached 1 items, skipped 2 existing" in stage_log(completed, "text_cache")
        assert stage(completed, "latent_cache")["status"] == "reused"
        assert stage(completed, "latent_cache")["reused_from"] == stopped["id"]
        assert not (directory / "latent_cache.log").exists()
        assert stage(completed, "text_cache")["status"] == "completed"
        assert "Caching 1 changed items: still0.png." in stage(completed, "text_cache")["cache_reason"]

        # The source Run is unchanged.
        assert unchanged_fields(service.get(stopped["id"])) == source
        assert item_caches(service.get(stopped["id"])) == source_caches

        # Compare shows the caption difference between the segments, without the snapshots' own paths.
        comparison = compare_runs(service.get(stopped["id"]), completed)
        paths = [change["path"] for change in comparison["changes"]]
        assert f"dataset.items.{edited['id']}.caption.text" in paths
        assert not any("snapshot_path" in path for path in paths)
        assert comparison["dataset"] == ["1 caption changed"]
    finally:
        service.close()


def test_added_media_is_cached_alone_and_moved_items_keep_their_caches(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=40, save_every=40, save_state=True, sample_enabled=False)
    add_stills(root, job_id, "still0:red", "still1:green")
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        stopped, step = stopped_with_state(service, job_id)
        source_caches = item_caches(stopped)
        instant_steps(root)
        # Sorts between the two stills, so the second still moves to a new place in the snapshot.
        add_stills(root, job_id, "still05:yellow")
        revision = edit(root, job_id, repeats=2)
        check = service.resume_check(stopped["id"])
        assert check["compatible"] and check["reprepare"], check
        assert [(change["field"], change["tier"]) for change in check["changes"]] == [("repeats", "reprepare")]
        assert check["dataset_changes"]["summary"] == ["1 media added"]
        assert check["preparation"]["latent_cache"] == {"reuse": 3, "rebuild": ["still05.png"]}
        assert check["preparation"]["text_cache"] == {"reuse": 3, "rebuild": ["still05.png"]}

        completed = wait_for(service, service.resume(stopped["id"], revision)["id"], "completed", device_released=True)
        assert completed["segment"]["step_offset"] == step
        assert completed["job"]["config"]["repeats"] == 2
        assert "num_repeats = 2" in (Path(completed["directory"]) / "dataset.toml").read_text(encoding="utf-8")
        caches = item_caches(completed)
        added = item_id(completed, "still05.png")
        assert {key for key in caches if key[0] == added} == {
            (added, "0256x0256_wan.safetensors"),
            (added, "wan_te.safetensors"),
        }
        assert {key: value for key, value in caches.items() if key[0] != added} == source_caches
        moved = next(item for item in completed["manifest"]["items"] if item["name"] == "still1.png")
        before = next(item for item in stopped["manifest"]["items"] if item["name"] == "still1.png")
        assert Path(moved["snapshot_path"]).stem != Path(before["snapshot_path"]).stem
        for name in ("latent_cache", "text_cache"):
            assert "cached 1 items, skipped 3 existing" in stage_log(completed, name)
        assert "dataset.items." + added in [change["path"] for change in compare_runs(stopped, completed)["changes"]]
    finally:
        service.close()


def test_unchanged_datasets_keep_reading_the_source_snapshot(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=40, save_every=40, save_state=True, sample_enabled=False)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        stopped, _ = stopped_with_state(service, job_id)
        instant_steps(root)
        check = service.resume_check(stopped["id"])
        assert check["compatible"] and not check["reprepare"]
        assert check["dataset_changes"]["changed"] is False and check["preparation"] is None
        resumed = service.resume(stopped["id"], check["revision"])
        assert resumed["status"] == "pending_training" and "refresh" not in resumed
        assert resumed["cache"] == stopped["cache"]
    finally:
        service.close()


def test_an_unregistered_dataset_resumes_from_the_frozen_snapshot_with_a_note(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=40, save_every=40, save_state=True, sample_enabled=False)
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        stopped, _ = stopped_with_state(service, job_id)
        dataset_id = JobStore(root).get(job_id).datasets[0].dataset_id
        DatasetStore(root).remove(dataset_id)
        check = service.resume_check(stopped["id"])
        assert check["compatible"] and not check["reprepare"]
        assert any("keeps the frozen snapshot" in note for note in check["notes"])
    finally:
        service.close()


def test_alternating_wan_resumes_its_paired_states_with_a_refreshed_caption(tmp_path: Path) -> None:
    root, job_id = paired_job(
        tmp_path, steps=20, expert_interval=10, save_every=10, wan_steps_per_expert=True, sample_enabled=False
    )
    folder = add_stills(root, job_id, "still0:red")
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        stopped, step = stopped_with_state(service, job_id)
        source_caches = item_caches(stopped)
        instant_steps(root)
        (folder / "still0.txt").write_text(NEW_CAPTION, encoding="utf-8")
        revision = edit(root, job_id, steps=30)
        check = service.resume_check(stopped["id"], revision)
        assert check["compatible"] and check["reprepare"], check
        completed = wait_for(service, service.resume(stopped["id"], revision)["id"], "completed", device_released=True)
        assert completed["segment"]["schedule_start"] == step
        assert stage(completed, "latent_cache")["status"] == "reused"
        assert "cached 1 items, skipped 1 existing" in stage_log(completed, "text_cache")
        changed = (item_id(completed, "still0.png"), "wan_te.safetensors")
        caches = item_caches(completed)
        assert {key: value for key, value in caches.items() if key != changed} == {
            key: value for key, value in source_caches.items() if key != changed
        }
        dataset = str(Path(completed["directory"]) / "dataset.toml")
        assert {argument(entry["args"], "--dataset_config") for entry in completed["stages"]} == {dataset}
        assert {expert: state["optimizer_step"] for expert, state in completed["expert_states"].items()} == {
            "low": 30,
            "high": 30,
        }
    finally:
        service.close()


def test_a_source_cache_file_changed_since_is_cached_again_instead_of_copied(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=40, save_every=40, save_state=True, sample_enabled=False)
    folder = add_stills(root, job_id, "still0:red", "still1:green")
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        stopped, _ = stopped_with_state(service, job_id)
        instant_steps(root)
        (folder / "still0.txt").write_text(NEW_CAPTION, encoding="utf-8")
        still1 = next(item for item in stopped["manifest"]["items"] if item["name"] == "still1.png")
        damaged = next(Path(stopped["cache"]).glob(f"dataset*/{Path(still1['snapshot_path']).stem}_*_wan.safetensors"))
        damaged.write_bytes(damaged.read_bytes() + b"damaged")

        completed = wait_for(service, service.resume(stopped["id"], 2)["id"], "completed", device_released=True)
        latent = stage(completed, "latent_cache")
        assert latent["status"] == "completed"
        assert "1 changed or went missing there and are cached again" in latent["cache_reason"]
        assert "cached 1 items, skipped 2 existing" in stage_log(completed, "latent_cache")
        assert "cached 1 items, skipped 2 existing" in stage_log(completed, "text_cache")
    finally:
        service.close()

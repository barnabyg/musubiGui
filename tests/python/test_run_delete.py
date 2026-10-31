import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui import runs as runs_module
from musubi_gui.acceptance import prepare as prepare_acceptance
from musubi_gui.jobs import JobStore
from musubi_gui.jobs_api import jobs_router
from musubi_gui.outputs import output_router
from musubi_gui.processes import process_identity
from musubi_gui.run_store import ACTIVE_STATUSES
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings, load_settings, save_settings


def wait_for(service: RunService, run_id: str, status: str, *, device_released: bool = False) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == status and (not device_released or service.device.owner() is None):
            return run
        time.sleep(0.02)
    raise AssertionError(service.get(run_id))


def client_for(root: Path, service: RunService) -> TestClient:
    from musubi_gui.runs_api import runs_router

    app = FastAPI()
    app.include_router(jobs_router(root, fixture_mode=service.fixture_mode))
    app.include_router(runs_router(service))
    app.include_router(output_router(service))
    return TestClient(app)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    save_settings(root, Settings(output_root=str(tmp_path / "output"), cache_root=str(tmp_path / "cache")))
    return root


@pytest.fixture
def service(state: Path, tmp_path: Path) -> Iterator[RunService]:
    running = RunService(state, device_path=tmp_path / "device.sqlite", autostart=False)
    yield running
    running.close()


def add_run(service: RunService, token: str, status: str = "completed", **details: Any) -> dict[str, Any]:
    """A Run with real files in the layout preparation produces."""
    settings = load_settings(service.root)
    directory = Path(settings.output_root) / "runs" / token
    cache = Path(settings.cache_root) / "runs" / token
    (directory / "checkpoints").mkdir(parents=True)
    (directory / "checkpoints" / "trained.safetensors").write_bytes(b"x" * 1000)
    cache.mkdir(parents=True)
    (cache / "latents.safetensors").write_bytes(b"y" * 500)
    run = service.submit(
        ExecutionPlan(
            job={"id": "a" * 32, "name": token, "family": "wan", "revision": 1},
            settings=settings,
            manifest={"items": []},
            directory=directory,
            cache=cache,
            stages=[],
            fingerprint=token,
        )
    )
    return service.store.update(run["id"], lambda record: record.update(status=status, **details))


def folders(run: dict[str, Any]) -> tuple[Path, Path]:
    return Path(run["directory"]), Path(run["cache"])


def size(*paths: str | Path) -> int:
    return sum(file.stat().st_size for path in paths for file in Path(path).rglob("*") if file.is_file())


def junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$null = New-Item -ItemType Junction -Path '{link}' -Target '{target}'",
        ],
        check=True,
        capture_output=True,
    )


def test_deleting_a_run_removes_its_record_folders_and_output_index(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    run, other = add_run(service, "first"), add_run(service, "second")
    assert len(client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]) == 1
    assert len(list((state / "output-index").iterdir())) == 1
    expected = size(*folders(run))

    deleted = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": run["id"], "freed_bytes": expected}
    assert not any(folder.exists() for folder in folders(run))
    assert all(folder.exists() for folder in folders(other))
    assert [item["id"] for item in client.get("/api/runs").json()] == [other["id"]]
    assert client.get(f"/api/runs/{run['id']}").status_code == 409
    assert list((state / "output-index").iterdir()) == []
    assert client.get(f"/api/runs/{run['id']}/outputs").status_code == 409


def test_a_delete_must_be_confirmed(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    refused = client.post(f"/api/runs/{run['id']}/delete", json={})
    assert refused.status_code == 409
    assert "confirm" in refused.json()["detail"]
    assert all(folder.exists() for folder in folders(run))


def test_the_preview_shows_the_disk_space_and_folders_without_changing_anything(
    state: Path, service: RunService
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    preview = client.get(f"/api/runs/{run['id']}/delete-preview").json()
    assert preview["refusal"] is None
    assert preview["bytes"] == size(*folders(run)) > 1500
    assert preview["folders"] == [run["directory"], run["cache"]]
    assert preview["shared_cache"] is None
    assert service.get(run["id"])["status"] == "completed"
    assert all(folder.exists() for folder in folders(run))


@pytest.mark.parametrize("status", sorted(ACTIVE_STATUSES - {"orphaned", "uncertain"}))
def test_an_active_run_is_refused_with_the_reason(state: Path, service: RunService, status: str) -> None:
    client = client_for(state, service)
    # A live controller, or reconciling would rightly find a working status abandoned.
    run = add_run(service, "first", status=status, controller=process_identity())
    refused = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    assert "active" in refused.json()["detail"]
    assert "active" in client.get(f"/api/runs/{run['id']}/delete-preview").json()["refusal"]
    assert service.get(run["id"])["status"] == status
    assert all(folder.exists() for folder in folders(run))


@pytest.mark.parametrize("status", ["orphaned", "uncertain", "interrupted"])
def test_a_run_with_live_processes_to_reconcile_is_refused(state: Path, service: RunService, status: str) -> None:
    client = client_for(state, service)
    run = add_run(service, "first", status=status, processes=[process_identity()])
    refused = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    assert "Reconcile" in refused.json()["detail"]
    assert all(folder.exists() for folder in folders(run))


@pytest.mark.parametrize("status", ["pending_training", "prepared"])
def test_a_run_that_a_waiting_segment_needs_is_refused(state: Path, service: RunService, status: str) -> None:
    client = client_for(state, service)
    source = add_run(service, "source")
    state_path = str(Path(source["directory"]) / "checkpoints" / "lora-state")
    segment = add_run(
        service, "segment", status=status, resume_of=source["id"], state_source={"path": state_path, "files": {}}
    )
    refused = client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    assert segment["id"][:8] in refused.json()["detail"]
    assert "needs" in refused.json()["detail"]
    assert all(folder.exists() for folder in folders(source))


def test_a_prepared_retry_keeps_the_run_whose_frozen_media_it_reads(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    source = add_run(service, "source", status="failed")
    media = str(Path(source["directory"]) / "media" / "item000000.mp4")
    add_run(service, "retry", status="prepared", retry_of=source["id"], input_files={media: "digest"})
    assert client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True}).status_code == 409


def test_a_stage_argument_counts_as_needing_the_run(state: Path, service: RunService) -> None:
    # A retry of a cancelled segment has no state_source; only its --resume argument points at the source.
    client = client_for(state, service)
    source = add_run(service, "source")
    state_path = str(Path(source["directory"]) / "checkpoints" / "lora-state")
    stage = {"name": "train", "args": ["python", "--resume", state_path], "status": "pending"}
    add_run(service, "retry", status="prepared", stages=[stage])
    assert client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True}).status_code == 409


def test_a_similar_folder_name_is_not_a_dependency(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    source = add_run(service, "source")
    other = str(Path(source["directory"] + "-other") / "media.mp4")
    add_run(service, "unrelated", status="prepared", input_files={other: "digest"})
    assert client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True}).status_code == 200


def test_a_finished_segment_survives_its_source_and_shows_the_lost_lineage(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    source = add_run(service, "source")
    media = str(Path(source["directory"]) / "media" / "item000000.mp4")
    segment = add_run(service, "segment", resume_of=source["id"], input_files={media: "digest"})
    service.store.update(segment["id"], lambda record: record.update(cache=source["cache"]))

    preview = client.get(f"/api/runs/{source['id']}/delete-preview").json()
    assert preview["refusal"] is None
    assert [item["id"] for item in preview["dependents"]] == [segment["id"]]
    # The segment shares the source's cache folder, so that stays until the last user is deleted.
    assert preview["folders"] == [source["directory"]]
    assert preview["shared_cache"] == source["cache"]
    assert preview["bytes"] == size(source["directory"])

    assert client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True}).json()["deleted"]

    assert not Path(source["directory"]).exists()
    assert Path(source["cache"]).exists()
    assert Path(segment["directory"]).exists()
    remaining = client.get(f"/api/runs/{segment['id']}").json()
    assert remaining["resume_of"] == source["id"]
    assert client.get(f"/api/runs/{segment['id']}/outputs").json()["outputs"][0]["name"] == "trained.safetensors"
    assert client.get(f"/api/runs/{segment['id']}/logs").status_code == 200

    assert client.post(f"/api/runs/{segment['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert not Path(source["cache"]).exists()


def test_deleting_a_finished_segment_keeps_the_cache_it_shares_with_its_source(
    state: Path, service: RunService
) -> None:
    client = client_for(state, service)
    source = add_run(service, "source")
    segment = add_run(service, "segment", resume_of=source["id"])
    own_cache = Path(segment["cache"])
    service.store.update(segment["id"], lambda record: record.update(cache=source["cache"]))
    assert client.post(f"/api/runs/{segment['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert not Path(segment["directory"]).exists()
    assert own_cache.exists()  # Recorded nowhere any more: this Run's cache is the shared one.
    assert all(folder.exists() for folder in folders(source))
    assert service.get(source["id"])["status"] == "completed"


@pytest.mark.skipif(os.name != "nt", reason="Only Windows refuses to delete an open file")
def test_a_locked_file_leaves_the_run_visible_and_it_can_be_deleted_again(
    state: Path, service: RunService, tmp_path: Path
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first", status="prepared")
    directory, cache = folders(run)
    locked = directory / "checkpoints" / "trained.safetensors"
    (directory / "other.log").write_text("removed even though the checkpoint is locked", encoding="utf-8")
    with locked.open("rb"):
        result = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})
        assert result.status_code == 200
        body = result.json()
        assert body["deleted"] is False
        assert body["run"]["status"] == "deleting"
        assert "trained.safetensors" in body["run"]["deletion"]["error"]
        assert body["run"]["deletion"]["remaining"] == [{"path": str(directory), "bytes": 1000}]
        assert locked.exists() and not (directory / "other.log").exists() and not cache.exists()

        # A half-deleted Run can't be used, and its outputs are not offered.
        assert client.post(f"/api/runs/{run['id']}/start", json={}).status_code == 409
        assert client.post(f"/api/runs/{run['id']}/retry", json={}).status_code == 409
        scan = client.get(f"/api/runs/{run['id']}/outputs").json()
        assert scan["outputs"] == [] and "being deleted" in scan["warnings"][0]

        again = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()
        assert again["deleted"] is False

    restarted = RunService(state, device_path=tmp_path / "device.sqlite", autostart=False)
    try:
        client = client_for(state, restarted)
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "deleting"
        assert client.get(f"/api/runs/{run['id']}/delete-preview").json()["bytes"] == 1000
        assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
        assert not directory.exists()
        assert client.get("/api/runs").json() == []
    finally:
        restarted.close()


@pytest.mark.skipif(os.name != "nt", reason="Only Windows refuses to delete an open file")
def test_a_briefly_held_file_does_not_fail_the_delete(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    stream = (Path(run["directory"]) / "checkpoints" / "trained.safetensors").open("rb")
    threading.Timer(0.2, stream.close).start()
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert not Path(run["directory"]).exists()


def test_a_crash_between_folders_lists_only_what_is_left(
    state: Path, service: RunService, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    real = runs_module.remove_folder

    def crash_on_cache(folder: Path) -> list[str]:
        if folder == Path(run["cache"]):
            raise KeyboardInterrupt
        return real(folder)

    monkeypatch.setattr(runs_module, "remove_folder", crash_on_cache)
    with pytest.raises(KeyboardInterrupt):
        service.delete(run["id"], confirmed=True)
    monkeypatch.undo()
    assert service.get(run["id"])["deletion"]["remaining"] == [{"path": run["cache"]}]
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert not Path(run["cache"]).exists()


def test_comparison_copes_with_a_deleted_run(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    run, other, third = add_run(service, "first"), add_run(service, "second"), add_run(service, "third")
    client.get(f"/api/runs/{other['id']}/outputs")
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert client.get(f"/api/runs/{other['id']}/compare/{third['id']}").status_code == 200
    assert "not found" in client.get(f"/api/runs/{other['id']}/compare/{run['id']}").json()["detail"]
    assert client.get(f"/api/runs/{other['id']}/outputs").status_code == 200


def test_a_delete_interrupted_by_a_restart_stays_visible_and_finishes(
    state: Path, service: RunService, tmp_path: Path
) -> None:
    run = add_run(service, "first", status="deleting")
    directory, cache = folders(run)
    restarted = RunService(state, device_path=tmp_path / "device.sqlite")
    try:
        client = client_for(state, restarted)
        assert client.get(f"/api/runs/{run['id']}").json()["status"] == "deleting"
        assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
        assert not directory.exists() and not cache.exists()
    finally:
        restarted.close()


def test_a_delete_interrupted_after_removing_the_folders_finishes(state: Path, service: RunService) -> None:
    import shutil

    client = client_for(state, service)
    run = add_run(service, "first", status="deleting")
    for folder in folders(run):
        shutil.rmtree(folder)
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert client.get("/api/runs").json() == []


def test_a_run_under_a_root_no_longer_configured_is_refused_until_the_root_returns(
    state: Path, service: RunService, tmp_path: Path
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    original = load_settings(state)
    save_settings(state, Settings(output_root=str(tmp_path / "moved"), cache_root=original.cache_root))
    refused = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    assert "Restore" in refused.json()["detail"]
    assert all(folder.exists() for folder in folders(run))
    assert service.get(run["id"])["status"] == "completed"
    save_settings(state, Settings(output_root=original.output_root, cache_root=str(tmp_path / "moved")))
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).status_code == 409
    assert all(folder.exists() for folder in folders(run))
    save_settings(state, original)
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]


@pytest.mark.parametrize(
    ("field", "target"),
    [
        ("directory", "output"),
        ("directory", "output/runs"),
        ("directory", "output/runs/first/checkpoints"),
        ("directory", "outside"),
        ("cache", "cache"),
        ("cache", "cache/runs"),
        ("cache", "cache/shared/wan-umt5-tokenizer"),
        ("cache", "output/runs/first"),
    ],
)
def test_a_recorded_folder_that_is_not_a_run_folder_is_refused(
    state: Path, service: RunService, tmp_path: Path, field: str, target: str
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    protected = tmp_path / target
    protected.mkdir(parents=True, exist_ok=True)
    (protected / "keep.txt").write_text("keep", encoding="utf-8")
    service.store.update(run["id"], lambda record: record.update({field: str(protected)}))
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).status_code == 409
    assert (protected / "keep.txt").exists()
    assert service.get(run["id"])["status"] == "completed"


def test_a_run_folder_replaced_by_a_link_is_refused_and_its_target_survives(
    state: Path, service: RunService, tmp_path: Path
) -> None:
    import shutil

    client = client_for(state, service)
    run = add_run(service, "first")
    other = add_run(service, "other")
    shutil.rmtree(run["directory"])
    junction(Path(run["directory"]), Path(other["directory"]))
    refused = client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    assert "link" in refused.json()["detail"]
    assert (Path(other["directory"]) / "checkpoints" / "trained.safetensors").exists()


def test_a_link_inside_a_run_folder_is_removed_without_following_it(
    state: Path, service: RunService, tmp_path: Path
) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.bin").write_bytes(b"z" * 5000)
    expected = size(*folders(run))
    junction(Path(run["directory"]) / "escape", outside)
    assert client.get(f"/api/runs/{run['id']}/delete-preview").json()["bytes"] == expected
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert not Path(run["directory"]).exists()
    assert (outside / "private.bin").read_bytes() == b"z" * 5000


def test_a_new_run_cannot_start_reading_from_a_run_being_deleted(state: Path, service: RunService) -> None:
    source = add_run(service, "source", status="deleting")
    media = str(Path(source["directory"]) / "media" / "item000000.mp4")
    record = {"id": "retry", "job": {"id": "x"}, "status": "prepared", "fingerprint": "r", "input_files": {media: "d"}}
    with pytest.raises(ValueError, match="being deleted"):
        service.store.create(record)
    assert [run["id"] for run in service.list()] == [source["id"]]


def test_an_unknown_run_database_version_stays_blocked(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    run = add_run(service, "first")
    with sqlite3.connect(state / "runs.sqlite3") as connection:
        connection.execute("PRAGMA user_version=2")
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).status_code == 409
    assert all(folder.exists() for folder in folders(run))
    connection.close()


def test_prepare_creates_a_new_run_after_a_delete_and_other_runs_keep_working(tmp_path: Path) -> None:
    fixture = prepare_acceptance(tmp_path / "acceptance", "fixture", None)
    root = Path(fixture["state"])
    running = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        client = client_for(root, running)
        job = fixture["jobs"][0]
        settings = load_settings(root)
        first = running.prepare(job["id"])
        wait_for(running, first["id"], "prepared")
        running.start(first["id"])
        wait_for(running, first["id"], "completed", device_released=True)
        tokenizer = Path(settings.cache_root) / "shared" / "wan-umt5-tokenizer"
        tokenizer.mkdir(parents=True, exist_ok=True)
        (tokenizer / "marker.txt").write_text("shared", encoding="utf-8")
        preset = client.post(f"/api/jobs/{job['id']}/presets", json={"name": "kept"}).json()
        dataset_files = {file: file.read_bytes() for file in Path(fixture["workspace"]).glob("datasets/**/*.*")}
        assert dataset_files

        assert running.prepare(job["id"])["existing"] is True
        assert client.post(f"/api/runs/{first['id']}/delete", json={"confirmed": True}).json()["deleted"]

        assert (tokenizer / "marker.txt").exists()
        assert preset["id"] in [item["id"] for item in client.get("/api/jobs/presets").json()]
        assert {file: file.read_bytes() for file in dataset_files} == dataset_files
        assert [item["id"] for item in client.get("/api/jobs").json()].count(job["id"]) == 1

        second = running.prepare(job["id"])
        assert second["existing"] is False
        assert second["id"] != first["id"]
        prepared = wait_for(running, second["id"], "prepared", device_released=True)
        assert all(stage["status"] != "reused" for stage in prepared["stages"] if stage["name"].endswith("_cache"))
    finally:
        running.close()


def test_a_run_that_reused_a_deleted_runs_cache_keeps_working(tmp_path: Path) -> None:
    fixture = prepare_acceptance(tmp_path / "acceptance", "fixture", None)
    root = Path(fixture["state"])
    running = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        client = client_for(root, running)
        job = fixture["jobs"][0]
        first = running.prepare(job["id"])
        wait_for(running, first["id"], "prepared", device_released=True)
        running.store.update(first["id"], lambda record: record.update(status="stopped"))
        second = running.prepare(job["id"])
        prepared = wait_for(running, second["id"], "prepared", device_released=True)
        assert {stage.get("reused_from") for stage in prepared["stages"] if stage["name"].endswith("_cache")} == {
            first["id"]
        }

        assert client.post(f"/api/runs/{first['id']}/delete", json={"confirmed": True}).json()["deleted"]

        browsed = client.get(f"/api/runs/{second['id']}").json()
        assert first["id"] in {stage.get("reused_from") for stage in browsed["stages"]}
        assert all(Path(browsed["cache"], item["path"]).is_file() for item in browsed["stages"][0]["artifacts"])
        assert client.get(f"/api/runs/{second['id']}/outputs").status_code == 200
        assert client.post(f"/api/runs/{second['id']}/start", json={}).status_code == 200
        wait_for(running, second["id"], "completed", device_released=True)
    finally:
        running.close()


def test_a_real_restored_segment_blocks_its_source_until_finished_then_outlives_it(tmp_path: Path) -> None:
    fixture = prepare_acceptance(tmp_path / "acceptance", "fixture", None)
    root = Path(fixture["state"])
    running = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        client = client_for(root, running)
        source = running.prepare(fixture["jobs"][0]["id"])
        wait_for(running, source["id"], "prepared", device_released=True)
        running.start(source["id"])
        wait_for(running, source["id"], "completed", device_released=True)
        state = running.saved_states(source["id"])[0]

        # Another owner of the GPU keeps the restored segment waiting.
        assert running.device.acquire("other-work", {})
        segment = running.restore_state(source["id"], state["id"], 1, confirmed=True)
        assert segment["status"] == "pending_training"
        assert segment["cache"] == source["cache"]
        refused = client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True})
        assert refused.status_code == 409
        assert segment["id"][:8] in refused.json()["detail"]
        assert Path(state["path"]).is_dir()
        running.device.release("other-work")
        wait_for(running, segment["id"], "completed", device_released=True)

        preview = client.get(f"/api/runs/{source['id']}/delete-preview").json()
        assert preview["shared_cache"] == source["cache"]
        assert [item["id"] for item in preview["dependents"]] == [segment["id"]]
        assert client.post(f"/api/runs/{source['id']}/delete", json={"confirmed": True}).json()["deleted"]

        assert not Path(source["directory"]).exists()
        assert Path(source["cache"]).is_dir()
        assert client.get(f"/api/runs/{segment['id']}").json()["resume_of"] == source["id"]
        names = [item["path"] for item in client.get(f"/api/runs/{segment['id']}/outputs").json()["outputs"]]
        assert any(name.startswith("checkpoints/") for name in names)
        kept = running.saved_states(segment["id"])[0]
        refused = client.post(
            f"/api/runs/{segment['id']}/restore-state",
            json={"state_id": kept["id"], "additional_steps": 1, "confirmed": True},
        )
        assert refused.status_code == 409
        assert "Frozen dataset inputs" in refused.json()["detail"]

        assert client.post(f"/api/runs/{segment['id']}/delete", json={"confirmed": True}).json()["deleted"]
        assert not Path(source["cache"]).exists()
    finally:
        running.close()


def test_a_cache_being_deleted_is_not_reused_and_a_vanishing_one_falls_through(
    state: Path, service: RunService, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = {"stage_keys": {"latent_cache": "same"}}
    artifact = {"path": "latents.safetensors", "sha256": ""}
    from musubi_gui import runs
    from musubi_gui.snapshots import digest_file

    def add_with_cache(token: str, status: str) -> dict[str, Any]:
        run = add_run(service, token, status=status, **key)
        file = Path(run["cache"]) / artifact["path"]
        digest = {**artifact, "sha256": digest_file(file)}
        stage = {"name": "latent_cache", "status": "completed", "artifacts": [digest]}
        return service.store.update(run["id"], lambda record: record.update(stages=[stage]))

    deleting = add_with_cache("deleting", "deleting")
    target = add_with_cache("target", "preparing")
    assert service._reuse(target, "latent_cache", 0) is False

    service.store.update(deleting["id"], lambda record: record.update(status="completed"))

    def vanished(*_: object) -> None:
        raise FileNotFoundError("deleted meanwhile")

    monkeypatch.setattr(runs.shutil, "copyfile", vanished)
    assert service._reuse(target, "latent_cache", 0) is False


def job_client(tmp_path: Path) -> tuple[TestClient, RunService]:
    root = tmp_path / "state"
    running = RunService(root, device_path=tmp_path / "device.sqlite", autostart=False)
    return client_for(root, running), running


def new_job(client: TestClient, name: str = "tune-A1") -> dict[str, Any]:
    return dict(client.post("/api/jobs", json={"name": name, "family": "wan"}).json())


def test_a_job_without_runs_is_deleted_with_every_revision(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job, kept = new_job(client), new_job(client, "keeper")
    client.put(f"/api/jobs/{job['id']}", json={"name": "second", "family": "wan", "revision": 1})
    client.post(f"/api/jobs/{job['id']}/presets", json={"name": "saved"})
    assert client.get(f"/api/jobs/{job['id']}/delete-preview").json() == {"runs": 0, "refusal": None}

    deleted = client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True})

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": job["id"]}
    assert not (running.root / "jobs" / job["id"]).exists()
    assert [item["id"] for item in client.get("/api/jobs").json()] == [kept["id"]]
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert "saved" in [item["name"] for item in client.get("/api/jobs/presets").json()]
    running.close()


def test_a_job_delete_must_be_confirmed_and_a_missing_job_is_not_found(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job = new_job(client)
    assert client.post(f"/api/jobs/{job['id']}/delete", json={}).status_code == 422
    assert client.get(f"/api/jobs/{job['id']}").status_code == 200
    assert client.post(f"/api/jobs/{'b' * 32}/delete", json={"confirmed": True}).status_code == 404
    running.close()


@pytest.mark.parametrize("status", ["completed", "failed", "training", "deleting"])
def test_a_job_with_any_run_left_is_refused_and_points_to_the_alternatives(tmp_path: Path, status: str) -> None:
    client, running = job_client(tmp_path)
    job = new_job(client)
    run = running.store.create(
        {"id": "run-one", "job": {"id": job["id"]}, "status": status, "fingerprint": "one", "stages": []}
    )
    preview = client.get(f"/api/jobs/{job['id']}/delete-preview").json()
    assert preview["runs"] == 1
    refused = client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True})
    assert refused.status_code == 409
    for reason in [preview["refusal"], refused.json()["detail"]]:
        assert "Delete run" in reason and "Archive job" in reason
    assert client.get(f"/api/jobs/{job['id']}").status_code == 200
    assert running.get(run["id"])["status"] == status
    running.close()


def test_an_archived_job_can_be_deleted_and_leaves_no_marker(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job = new_job(client)
    client.post(f"/api/jobs/{job['id']}/archive", json={})
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 200
    assert client.get("/api/jobs/archived").json() == []
    assert not (running.root / "archived-jobs" / f"{job['id']}.json").exists()
    running.close()


def test_an_archived_job_with_runs_is_refused_too(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job = new_job(client)
    client.post(f"/api/jobs/{job['id']}/archive", json={})
    running.store.create(
        {"id": "run-one", "job": {"id": job["id"]}, "status": "completed", "fingerprint": "one", "stages": []}
    )
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 409
    assert [item["id"] for item in client.get("/api/jobs/archived").json()] == [job["id"]]
    running.close()


def test_a_job_can_be_deleted_once_its_last_run_is_gone(state: Path, service: RunService) -> None:
    client = client_for(state, service)
    job = new_job(client)
    run = add_run(service, "first")
    service.store.update(run["id"], lambda record: record["job"].update(id=job["id"]))
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 409
    assert client.post(f"/api/runs/{run['id']}/delete", json={"confirmed": True}).json()["deleted"]
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 200


def test_unknown_storage_stays_blocked_for_a_job_delete(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job, other = new_job(client), new_job(client, "other")
    (running.root / "jobs" / job["id"] / "unknown.txt").write_text("future", encoding="utf-8")
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 422
    assert (running.root / "jobs" / job["id"] / "1.json").exists()

    running.store.list()
    with sqlite3.connect(running.store.path) as connection:
        connection.execute("PRAGMA user_version=2")
    connection.close()
    assert client.post(f"/api/jobs/{other['id']}/delete", json={"confirmed": True}).status_code == 422
    assert (running.root / "jobs" / other["id"] / "1.json").exists()
    running.close()


def test_an_unreadable_older_revision_blocks_the_delete_and_is_preserved(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job = new_job(client)
    client.put(f"/api/jobs/{job['id']}", json={"name": "second", "family": "wan", "revision": 1})
    older = running.root / "jobs" / job["id"] / "1.json"
    older.write_text('{"version": 99}', encoding="utf-8")
    assert client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True}).status_code == 422
    assert older.read_text(encoding="utf-8") == '{"version": 99}'
    assert (older.parent / "2.json").exists()
    running.close()


def test_existing_revision_files_are_unchanged_by_deleting_another_job(tmp_path: Path) -> None:
    client, running = job_client(tmp_path)
    job, kept = new_job(client), new_job(client, "keeper")
    file = running.root / "jobs" / kept["id"] / "1.json"
    before = file.read_bytes()
    client.post(f"/api/jobs/{job['id']}/delete", json={"confirmed": True})
    assert file.read_bytes() == before
    assert json.loads(before)["id"] == JobStore(running.root).get(kept["id"]).id
    running.close()

"""Resuming a saved state with an edited revision of the Job: stop, edit the prompt and steps, resume."""

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import instant_steps, wait_for
from test_run_control import handled, job_with, training
from test_wan_pair_runs import paired_job

from musubi_gui.app import create_app
from musubi_gui.job_models import JobDraft
from musubi_gui.job_store import JobStore
from musubi_gui.outputs import OutputBrowser
from musubi_gui.runs import RunService
from musubi_gui.step_offset import due_steps
from musubi_gui.wan_pair_plan import argument

OLD_PROMPT = "A red square moves across a blue background."
NEW_PROMPT = "A green circle drifts over a white field."


def edit(root: Path, job_id: str, **config: Any) -> int:
    """Save a new revision of the Job with `config` changed; returns its number."""
    jobs = JobStore(root)
    job = jobs.get(job_id)
    draft = JobDraft(
        name=job.name, family=job.family, datasets=job.datasets, config=job.config.model_copy(update=config)
    )
    return jobs.save(job_id, draft, job.revision).revision


def cache_files(run: dict[str, Any]) -> dict[str, tuple[int, str]]:
    """Every preparation cache artifact of `run`, with its modification time and content hash."""
    cache = Path(run["cache"])
    files = [*cache.glob("dataset*/*.safetensors"), *cache.glob("uncond.safetensors")]
    return {
        str(file.relative_to(cache)): (file.stat().st_mtime_ns, hashlib.sha256(file.read_bytes()).hexdigest())
        for file in files
    }


def train_log(run: dict[str, Any]) -> str:
    return (Path(run["directory"]) / "train.log").read_text(encoding="utf-8", errors="replace")


def wait_for_log(run: dict[str, Any], text: str) -> None:
    deadline = time.monotonic() + 180
    while text not in (train_log(run) if (Path(run["directory"]) / "train.log").is_file() else ""):
        assert time.monotonic() < deadline, text
        time.sleep(0.02)


def unchanged_fields(run: dict[str, Any]) -> dict[str, Any]:
    # Inspecting saved states records them on the Run; nothing else about the source Run changes.
    return {key: value for key, value in run.items() if key not in {"saved_states", "retention"}}


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_save_and_stop_then_resume_with_an_edited_prompt_and_more_steps(tmp_path: Path, family: str) -> None:
    root, job_id = job_with(
        tmp_path,
        family,
        steps=40,
        save_every=40,
        save_state=True,
        preview_every=2,
        sample_enabled=False,
        sample_prompt=OLD_PROMPT + (" A steady sine tone plays." if family == "h3" else ""),
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        # Stop after a preview with the old prompt.
        wait_for_log(run, "SYNTHETIC preview complete at step 2: " + OLD_PROMPT)
        service.control(run["id"], "save_and_stop")
        step = handled(service, run["id"], 1)["step"]
        stopped = wait_for(service, run["id"], "stopped", device_released=True)
        assert step >= 2
        caches = cache_files(stopped)
        assert caches
        service.saved_states(run["id"])
        source = unchanged_fields(service.get(run["id"]))

        instant_steps(root)
        prompt = NEW_PROMPT + (" A steady sine tone plays." if family == "h3" else "")
        revision = edit(root, job_id, sample_prompt=prompt, steps=step + 4)
        check = service.resume_check(run["id"])
        assert check["compatible"], check
        assert (check["revision"], check["frozen_revision"], check["saved_step"]) == (revision, 2, step)
        assert {(change["field"], change["tier"]) for change in check["changes"]} == {
            ("sample_prompt", "editable"),
            ("steps", "editable"),
        }
        assert check["state_name"].endswith(f"-step{step:08d}-state")

        resumed = service.resume(run["id"], revision)
        completed = wait_for(service, resumed["id"], "completed", device_released=True)

        # The segment records its own revision, its lineage and what changed.
        assert completed["job"]["revision"] == revision
        assert completed["job"]["config"]["sample_prompt"] == prompt
        assert completed["resume_of"] == run["id"]
        assert completed["segment"]["source_revision"] == 2
        assert completed["segment"]["step_offset"] == step
        assert completed["segment"]["target_steps"] == step + 4
        assert {change["field"] for change in completed["segment"]["changes"]} == {"sample_prompt", "steps"}

        # Previews from the saved step onwards use the new prompt, numbered by the overall step.
        previews = due_steps(2, step, 4)
        log = train_log(completed)
        for preview in previews:
            assert f"SYNTHETIC preview complete at step {preview}: {prompt}" in log
        assert OLD_PROMPT not in log
        directory = Path(completed["directory"])
        train = next(stage for stage in completed["stages"] if stage["name"] == "train")
        assert argument(train["args"], "--sample_prompts") == str(directory / "previews.json")
        assert str(directory / "previews.json") in completed["input_files"]
        assert str(Path(run["directory"]) / "previews.json") not in completed["input_files"]
        outputs = OutputBrowser(service).scan(completed["id"])["outputs"]
        assert sorted(
            (output["metadata"]["preview"]["step"], output["metadata"]["preview"]["prompt"])
            for output in outputs
            if "preview" in output.get("metadata", {})
        ) == [(preview, prompt) for preview in previews]
        progress = service.progress(completed["id"])
        assert progress and (progress["step"], progress["total"]) == (step + 4, step + 4)
        # Named after the revision that trained it.
        assert argument(train["args"], "--output_name").endswith(f"-r{revision}")

        # Caches were reused, not rebuilt: no preparation ran and no cache file changed.
        assert completed["cache"] == stopped["cache"]
        assert argument(train["args"], "--dataset_config") == str(Path(run["directory"]) / "dataset.toml")
        assert [stage for stage in completed["stages"] if stage["name"] not in {"train", "generate"}] == [
            stage for stage in stopped["stages"] if stage["name"] not in {"train", "generate"}
        ]
        assert not list(directory.glob("*cache*.log"))
        assert cache_files(completed) == caches

        # The source Run is unchanged and keeps its own revision.
        assert unchanged_fields(service.get(run["id"])) == source
        assert service.get(run["id"])["job"]["revision"] == 2
    finally:
        service.close()


def test_a_revision_needing_a_new_run_is_rejected_with_the_field_names(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=3, save_every=1, save_state=True, preview_every=0)
    instant_steps(root)
    assets = tmp_path / "assets"
    assets.mkdir()
    with TestClient(create_app(root, assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")) as client:
        run = client.post("/api/runs/prepare", json={"job_id": job_id, "start": True}).json()
        deadline = time.monotonic() + 180
        while client.get(f"/api/runs/{run['id']}").json()["status"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        revision = edit(root, job_id, rank=16, seed=7, steps=6)
        before = len(client.get("/api/runs").json())

        check = client.post(f"/api/runs/{run['id']}/resume-check", json={}).json()
        assert not check["compatible"]
        assert check["blocking"] == ["LoRA rank", "Seed"]
        # Job review shows the same comparison against this, the Job's latest resumable training.
        review = client.post(f"/api/jobs/{job_id}/review", json={}).json()["resume_compatibility"]
        assert (review["run_id"], review["saved_step"], review["blocking"]) == (run["id"], 3, ["LoRA rank", "Seed"])
        refused = client.post(f"/api/runs/{run['id']}/resume", json={"job_revision": revision})
        assert refused.status_code == 422
        assert refused.json()["detail"] == (
            f"Revision {revision} changes LoRA rank, Seed, which needs a new Run: "
            "the saved state was trained with the earlier values."
        )

        # A total at or before the saved step is refused too.
        steps_only = edit(root, job_id, rank=8, seed=42, steps=3)
        refused = client.post(f"/api/runs/{run['id']}/resume", json={"job_revision": steps_only})
        assert refused.status_code == 422
        assert refused.json()["detail"] == "Training steps: choose a total after the saved step 3."
        assert len(client.get("/api/runs").json()) == before

        # Like every other action, resuming refuses another site's request.
        cross = client.post(
            f"/api/runs/{run['id']}/resume",
            json={"job_revision": steps_only, "target_steps": 5},
            headers={"Origin": "https://example.invalid"},
        )
        assert cross.status_code == 403
        assert len(client.get("/api/runs").json()) == before

        # An explicit total past the saved step resumes, keeping the Run's own revision values.
        resumed = client.post(
            f"/api/runs/{run['id']}/resume", json={"job_revision": steps_only, "target_steps": 5}
        ).json()
        assert resumed["segment"]["target_steps"] == 5
        missing = client.post(f"/api/runs/{run['id']}/resume", json={"job_revision": 99})
        assert missing.status_code == 409
        assert missing.json()["detail"] == "Revision 99 of this Run's Job doesn't exist."


def test_alternating_wan_resumes_its_paired_states_with_an_edited_revision(tmp_path: Path) -> None:
    root, job_id = paired_job(
        tmp_path,
        steps=20,
        expert_interval=10,
        save_every=10,
        preview_every=10,
        wan_steps_per_expert=True,
        sample_enabled=False,
        sample_prompt=OLD_PROMPT,
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "gpu.sqlite")
    try:
        run = service.prepare(job_id, start=True)
        training(service, run["id"])
        service.control(run["id"], "save_and_stop")
        step = handled(service, run["id"], 1)["step"]
        assert step < 10
        wait_for(service, run["id"], "stopped", device_released=True)
        caches = cache_files(service.get(run["id"]))

        # Changing the expert schedule would need a new Run.
        rejected = edit(root, job_id, expert_interval=5)
        assert service.resume_check(run["id"])["blocking"] == ["Expert interval"]
        with pytest.raises(ValueError, match="Expert interval"):
            service.resume(run["id"], rejected)

        instant_steps(root)
        revision = edit(root, job_id, expert_interval=10, sample_prompt=NEW_PROMPT, steps=30)
        check = service.resume_check(run["id"], revision)
        assert check["compatible"], check
        source = next(state for state in service.saved_states(run["id"]) if state["id"] == check["state_id"])
        assert source["paired"] and source["counts"] == {"low": step, "high": 0}

        resumed = service.resume(run["id"], revision)
        completed = wait_for(service, resumed["id"], "completed", device_released=True)
        assert completed["job"]["revision"] == revision
        assert completed["resume_of"] == run["id"]
        assert completed["segment"]["schedule_start"] == step
        assert completed["pair_sources"]["low"]["path"] == source["sources"]["low"]["path"]
        # The schedule keeps its interval of ten: high catches up with low, then each expert switches at tens.
        assert [
            (
                stage["training"]["expert"],
                stage["training"][f"{stage['training']['expert']}_before"] + stage["training"]["steps"],
            )
            for stage in completed["stages"]
            if stage.get("training")
        ] == [("high", step), ("low", 10), ("high", 10), ("low", 20), ("high", 20), ("low", 30), ("high", 30)]
        assert {expert: state["optimizer_step"] for expert, state in completed["expert_states"].items()} == {
            "low": 30,
            "high": 30,
        }
        # Paired previews sample both experts with the new revision's prompt.
        generate = [stage for stage in completed["stages"] if stage["name"] == "generate"]
        assert [stage["preview_step"] for stage in generate] == [10, 20, 30]
        assert {argument(stage["args"], "--prompt") for stage in generate} == {NEW_PROMPT}
        assert [sample["preview_step"] for sample in completed["samples"]] == [10, 20, 30]
        assert cache_files(completed) == caches
        progress = service.progress(completed["id"])
        assert progress and (progress["step"], progress["total"]) == (30, 30)
    finally:
        service.close()


def test_a_changed_learning_rate_resumes_with_a_warning_and_reaches_the_trainer(tmp_path: Path) -> None:
    root, job_id = job_with(tmp_path, steps=3, save_every=1, save_state=True, preview_every=0, sample_enabled=False)
    instant_steps(root)
    assets = tmp_path / "assets"
    assets.mkdir()
    with TestClient(create_app(root, assets, fixture_mode=True, device_path=tmp_path / "device.sqlite")) as client:
        run = client.post("/api/runs/prepare", json={"job_id": job_id, "start": True}).json()
        deadline = time.monotonic() + 180
        while client.get(f"/api/runs/{run['id']}").json()["status"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        revision = edit(
            root,
            job_id,
            learning_rate=0.0002,
            timestep_sampling="sigmoid",
            sigmoid_scale=2.0,
            gradient_accumulation=2,
            steps=5,
        )

        check = client.post(f"/api/runs/{run['id']}/resume-check", json={}).json()
        assert check["compatible"], check
        assert [(change["field"], change["tier"]) for change in check["changes"]] == [
            ("gradient_accumulation", "warned"),
            ("learning_rate", "warned"),
            ("sigmoid_scale", "warned"),
            ("steps", "editable"),
            ("timestep_sampling", "warned"),
        ]
        assert len(check["warnings"]) == 2 and "what a step means" in check["warnings"][1]
        review = client.post(f"/api/jobs/{job_id}/review", json={}).json()["resume_compatibility"]
        assert review["compatible"] and review["warnings"] == check["warnings"]

        resumed = client.post(f"/api/runs/{run['id']}/resume", json={"job_revision": revision})
        assert resumed.status_code == 200, resumed.text
        segment_id = resumed.json()["id"]
        deadline = time.monotonic() + 180
        while (segment := client.get(f"/api/runs/{segment_id}").json())["status"] != "completed":
            assert segment["status"] not in {"failed", "stopped"}, segment
            assert time.monotonic() < deadline
            time.sleep(0.05)

    # The segment's own arguments carry the new values, consistent with its frozen revision.
    train = next(stage for stage in segment["stages"] if stage["name"] == "train")
    assert argument(train["args"], "--learning_rate") == "0.0002"
    assert argument(train["args"], "--gradient_accumulation_steps") == "2"
    assert argument(train["args"], "--timestep_sampling") == "sigmoid"
    assert argument(train["args"], "--sigmoid_scale") == "2.0"
    assert "--discrete_flow_shift" not in train["args"]
    assert {change["field"]: change["tier"] for change in segment["segment"]["changes"]}["learning_rate"] == "warned"
    # The trainer writes the configured rate over the restored one, and says so in the training log.
    assert "Resume learning rate: applied 0.0002; the saved state had 0.0001." in train_log(segment)
    progress = segment["segment"]
    assert (progress["step_offset"], progress["target_steps"]) == (3, 5)

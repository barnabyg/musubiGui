"""Which edits a resumed segment can take from a newer Job revision, and which need a new Run."""

from pathlib import Path
from typing import Any

import pytest

from musubi_gui.job_models import Job, JobConfig, JobDataset
from musubi_gui.resume_compat import EDITABLE, WARNED, changed_fields, compatibility, flatten, resume_review


def frozen(family: str = "wan", **config: Any) -> dict[str, Any]:
    """A Run's frozen revision, as `run["job"]` records it."""
    return Job(
        id="job",
        revision=2,
        name="Job",
        family=family,  # type: ignore[arg-type]
        datasets=[JobDataset(dataset_id="data")],
        config=JobConfig(steps=300, save_state=True, preview_every=100, **config),
    ).model_dump()


def edited(job: dict[str, Any], revision: int = 3, **changes: Any) -> dict[str, Any]:
    top = {key: changes.pop(key) for key in ["name", "family", "datasets"] if key in changes}
    return {**job, **top, "revision": revision, "config": {**job["config"], **changes}}


EDITABLE_CHANGES = {
    "steps": 600,
    "sample_enabled": False,
    "sample_prompt": "A blue circle.",
    "sample_steps": 8,
    "sample_width": 320,
    "sample_height": 192,
    "sample_frames": 33,
    "sample_memory_policy": "conservative",
    "preview_every": 50,
    "save_every": 25,
    "keep_checkpoints": 2,
    "blocks_to_swap": 30,
    "gradient_checkpointing": False,
    "offload_inactive_dit": True,
}
NEW_RUN_CHANGES = {
    "recipe": "alternating",
    "components": {"wan_low": "other.safetensors"},
    "rank": 16,
    "alpha": 16,
    "optimizer": "AdamW8bit",
    "fp8_scaled": False,
    "block_swap_h2d_only": True,
    "expert_interval": 10,
    "wan_steps_per_expert": False,
    "repeats": 2,
    "width": 320,
    "height": 320,
    "frames": 33,
    "frame_extraction": "uniform",
    "frame_stride": 8,
    "frame_sample": 2,
    "enable_bucket": True,
    "bucket_no_upscale": True,
    "batch_size": 2,
    "latent_cache_policy": "rebuild",
    "text_cache_policy": "rebuild",
    "seed": 7,
    # Separate ticket: these move to the warned tier once a resumed trainer applies them.
    "learning_rate": 0.0002,
    "timestep_sampling": "sigmoid",
    "sigmoid_scale": 2.0,
    "discrete_flow_shift": 5.0,
    "gradient_accumulation": 2,
    # Not in any tier's list, so it conservatively needs a new Run.
    "audio_policy": "allow-missing",
}


def test_an_unchanged_revision_is_compatible_with_no_changes() -> None:
    job = frozen()
    result = compatibility(job, edited(job, steps=300), saved_step=100, target_steps=300)
    assert result["compatible"]
    assert result["changes"] == []
    assert (result["frozen_revision"], result["revision"], result["saved_step"]) == (2, 3, 100)
    assert any("data order restarts" in note for note in result["notes"])


@pytest.mark.parametrize("field,value", EDITABLE_CHANGES.items())
def test_editable_fields_resume(field: str, value: Any) -> None:
    job = frozen()
    result = compatibility(job, edited(job, **{field: value}), saved_step=100, target_steps=600)
    assert result["compatible"], result
    assert [(change["field"], change["tier"]) for change in result["changes"]] == [(field, "editable")]
    assert result["changes"][0]["label"] == EDITABLE[field]


@pytest.mark.parametrize("field,value", NEW_RUN_CHANGES.items())
def test_fields_that_change_what_is_trained_need_a_new_run(field: str, value: Any) -> None:
    job = frozen()
    result = compatibility(job, edited(job, **{field: value}), saved_step=100, target_steps=600)
    assert not result["compatible"]
    [change] = result["changes"]
    assert (change["field"], change["tier"]) == (field, "new_run")
    assert result["blocking"] == [change["label"]]
    assert change["label"] != field or field not in EDITABLE


def test_the_warned_tier_is_empty_until_the_trainer_applies_those_fields() -> None:
    assert WARNED == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"family": "h3"},
        {"datasets": [{"dataset_id": "other"}]},
        {"datasets": [{"dataset_id": "data", "repeats": 3}]},
        {"datasets": [{"dataset_id": "data", "resolution": (320, 320)}]},
    ],
)
def test_family_and_datasets_need_a_new_run(changes: dict[str, Any]) -> None:
    job = frozen()
    result = compatibility(job, edited(job, **changes), saved_step=100, target_steps=600)
    assert not result["compatible"]
    assert [change["tier"] for change in result["changes"]] == ["new_run"]


def test_a_renamed_job_still_resumes() -> None:
    job = frozen()
    result = compatibility(job, edited(job, name="Renamed"), saved_step=100, target_steps=300)
    assert result["compatible"]
    assert [(change["field"], change["tier"]) for change in result["changes"]] == [("name", "editable")]


def test_turning_saved_state_off_needs_a_new_run_but_turning_it_on_does_not() -> None:
    on = frozen()
    assert not compatibility(on, edited(on, save_state=False), saved_step=100, target_steps=300)["compatible"]
    # Save and stop saves state even when the Job keeps none, so such a Run can resume and start keeping it.
    off = edited(on, revision=2, save_state=False)
    result = compatibility(off, edited(off, save_state=True), saved_step=100, target_steps=300)
    assert result["compatible"]
    assert [(change["field"], change["tier"]) for change in result["changes"]] == [("save_state", "editable")]


@pytest.mark.parametrize("target", [100, 50])
def test_the_total_must_exceed_the_saved_step(target: int) -> None:
    job = frozen()
    result = compatibility(job, edited(job, steps=target), saved_step=100, target_steps=target)
    assert not result["compatible"]
    assert result["errors"] == ["Training steps: choose a total after the saved step 100."]
    assert result["blocking"] == []


def test_an_unknown_saved_step_skips_the_total_check() -> None:
    job = frozen()
    result = compatibility(job, edited(job, steps=10), saved_step=None, target_steps=10)
    assert result["compatible"] and result["errors"] == []


def test_fields_added_after_a_run_was_frozen_compare_by_their_defaults() -> None:
    # A Run frozen before sample geometry existed reads as the default; only real edits count.
    job = frozen()
    old = {**job, "config": {key: value for key, value in job["config"].items() if key != "sample_memory_policy"}}
    assert compatibility(old, edited(job), saved_step=100, target_steps=300)["changes"] == []


def test_unknown_fields_need_a_new_run() -> None:
    [change] = changed_fields({"steps": 1, "future_field": 1}, {"steps": 1, "future_field": 2})
    assert (change.field, change.label, change.tier) == ("future_field", "future_field", "new_run")
    [added] = changed_fields({}, {"another_field": True})
    assert added.tier == "new_run"


def test_flatten_puts_job_fields_beside_config_fields() -> None:
    flat = flatten(frozen())
    assert flat["name"] == "Job" and flat["family"] == "wan" and flat["rank"] == 8
    assert flat["datasets"] == [{"dataset_id": "data", "repeats": None}]
    assert "id" not in flat and "revision" not in flat


def test_an_unreadable_frozen_revision_is_refused() -> None:
    with pytest.raises(ValueError, match="prepare a new Run"):
        compatibility({"family": "wan", "config": {"rank": "many"}}, frozen(), saved_step=1, target_steps=300)


def run_record(tmp_path: Path, name: str, created: str, **fields: Any) -> dict[str, Any]:
    directory = tmp_path / name
    (directory / "checkpoints").mkdir(parents=True)
    return {"id": name, "created": created, "status": "stopped", "directory": str(directory), "job": frozen(), **fields}


def test_job_review_compares_with_the_latest_resumable_training(tmp_path: Path) -> None:
    job = edited(frozen(), sample_prompt="New", rank=16)
    older = run_record(tmp_path, "older", "2026-01-01", saved_states=[{"valid": True, "resume_step": 40}])
    newer = run_record(tmp_path, "newer", "2026-01-02", saved_states=[{"valid": True, "resume_step": 80}])
    runs = [
        older,
        newer,
        # Not resumable: still training, another Job, or inspected without a valid state.
        run_record(tmp_path, "active", "2026-01-03", status="training"),
        run_record(tmp_path, "other", "2026-01-03", job={**frozen(), "id": "other"}),
        run_record(tmp_path, "invalid", "2026-01-03", saved_states=[{"valid": False, "error": "changed"}]),
    ]
    section = resume_review(runs, job)
    assert section is not None
    assert (section["run_id"], section["run_status"], section["saved_step"]) == ("newer", "stopped", 80)
    assert not section["compatible"]
    assert [(change["field"], change["tier"]) for change in section["changes"]] == [
        ("rank", "new_run"),
        ("sample_prompt", "editable"),
    ]


def test_job_review_uses_uninspected_state_folders_without_a_saved_step(tmp_path: Path) -> None:
    job = edited(frozen(), steps=10)
    run = run_record(tmp_path, "uninspected", "2026-01-01")
    assert resume_review([run], job) is None
    (Path(run["directory"]) / "checkpoints" / "Job-r2-step00000005-state").mkdir()
    section = resume_review([run], job)
    assert section is not None and section["compatible"] and section["saved_step"] is None


def test_job_review_reports_an_unreadable_frozen_revision(tmp_path: Path) -> None:
    run = run_record(tmp_path, "old", "2026-01-01", job={"id": "job", "config": {"rank": "many"}})
    run["saved_states"] = [{"valid": True, "resume_step": 3}]
    section = resume_review([run], edited(frozen()))
    assert section is not None and not section["compatible"]
    assert "prepare a new Run" in section["errors"][0]

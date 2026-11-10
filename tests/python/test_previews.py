import json
from pathlib import Path

import pytest
from helpers import acceptance_workspace, wait_for

from musubi_gui.jobs import JobConfig, JobDraft, JobStore, configuration_errors
from musubi_gui.outputs import OutputBrowser
from musubi_gui.preview import validate_previews
from musubi_gui.runs import RunService
from musubi_gui.snapshots import verify_inputs


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_native_previews_are_frozen_discovered_and_do_not_require_final_sampling(tmp_path: Path, family: str) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    jobs = JobStore(root)
    job = jobs.get(next(item["id"] for item in fixture["jobs"] if item["family"] == family))
    config = job.config.model_copy(
        update={
            "steps": 6,
            "preview_every": 2,
            "sample_enabled": False,
            "sample_prompt": "First line\nSecond --w 999 # literal prompt",
        }
    )
    job = jobs.save(
        job.id, JobDraft(name=job.name, family=job.family, datasets=job.datasets, config=config), job.revision
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job.id)
        prepared = wait_for(service, run["id"], "prepared")
        prompts = Path(prepared["directory"], "previews.json")
        assert str(prompts) in prepared["input_files"]
        assert json.loads(prompts.read_text(encoding="utf-8"))[0]["prompt"] == config.sample_prompt
        assert not any(stage["name"] == "generate" for stage in prepared["stages"])
        service.start(run["id"])
        completed = wait_for(service, run["id"], "completed")
        outputs = OutputBrowser(service).scan(run["id"])["outputs"]
        previews = [output for output in outputs if "preview" in output.get("metadata", {})]
        assert [output["metadata"]["preview"]["step"] for output in previews] == [2, 4, 6]
        assert all(output["status"] == "available" for output in previews)
        assert completed["checkpoints"]
        assert all(output["metadata"]["preview"]["prompt"] == config.sample_prompt for output in previews)
        last = Path(completed["directory"]) / previews[-1]["path"]
        last.unlink()
        args = next(stage["args"] for stage in completed["stages"] if stage["name"] == "train")
        with pytest.raises(ValueError, match="preview at step 6 is missing"):
            validate_previews(args, family)
        prompts.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="Frozen dataset inputs changed"):
            verify_inputs(completed)
    finally:
        service.close()


def test_preview_validation_preserves_old_defaults_and_rejects_negative_intervals() -> None:
    assert JobConfig().preview_every == 0
    old = JobDraft(name="Old", family="wan")
    assert not any("Preview interval" in error for error in configuration_errors(old))
    paired = old.model_copy(
        update={"config": JobConfig(recipe="alternating", preview_every=100, steps=500, save_state=True)}
    )
    assert not any("Preview interval" in error for error in configuration_errors(paired))
    invalid = old.model_copy(update={"config": JobConfig(preview_every=-1)})
    assert any("Preview interval" in error for error in configuration_errors(invalid))


def test_retry_owns_a_separate_sealed_prompt_file(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    jobs = JobStore(root)
    job = jobs.get(fixture["jobs"][0]["id"])
    job = jobs.save(
        job.id,
        JobDraft(
            name=job.name,
            family=job.family,
            datasets=job.datasets,
            config=job.config.model_copy(update={"preview_every": 1}),
        ),
        job.revision,
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False)
    try:
        original = service.prepare(job.id)
        service.cancel(original["id"])
        retried = service.retry(original["id"])
        prompt = Path(retried["directory"], "previews.json")
        assert str(prompt) in retried["input_files"]
        args = next(stage["args"] for stage in retried["stages"] if stage["name"] == "train")
        assert args[args.index("--sample_prompts") + 1] == str(prompt)
        Path(original["directory"], "previews.json").write_text("[]", encoding="utf-8")
        verify_inputs(retried)
    finally:
        service.close()

"""A restored segment continues the original Run's step numbering up to a chosen total."""

import json
from pathlib import Path

import pytest
from helpers import acceptance_workspace, instant_steps, wait_for

from musubi_gui.job_models import JobDraft
from musubi_gui.job_store import JobStore
from musubi_gui.outputs import OutputBrowser
from musubi_gui.runs import RunService


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_restored_segment_names_saves_and_previews_by_global_step(tmp_path: Path, family: str) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    instant_steps(root)
    jobs = JobStore(root)
    job = jobs.get(next(item["id"] for item in fixture["jobs"] if item["family"] == family))
    config = job.config.model_copy(
        update={
            "steps": 3,
            "save_every": 2,
            "save_state": True,
            "preview_every": 2,
            "sample_enabled": False,
            "keep_checkpoints": 1,
        }
    )
    job = jobs.save(
        job.id, JobDraft(name=job.name, family=job.family, datasets=job.datasets, config=config), job.revision
    )
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job.id, start=True)
        wait_for(service, run["id"], "completed", device_released=True)
        final = next(state for state in service.saved_states(run["id"]) if state["optimizer_step"] == 3)
        assert final["resume_step"] == 3
        with pytest.raises(ValueError, match="after the saved step 3"):
            service.restore_state(run["id"], final["id"], 3, confirmed=True)

        # Saved at 3 with intervals of 2: the segment trains global steps 4 to 8.
        restored = service.restore_state(run["id"], final["id"], 8, confirmed=True)
        assert restored["segment"]["target_steps"] == 8
        assert restored["segment"]["step_offset"] == 3
        train = next(stage for stage in restored["stages"] if stage["name"] == "train")
        assert train["args"][train["args"].index("--max_train_steps") + 1] == "5"
        resumed = wait_for(service, restored["id"], "completed", device_released=True)
        checkpoints = Path(resumed["directory"]) / "checkpoints"

        # Preview validation passed with previews named 4, 6 and 8.
        previews = sorted(file.name.split("_")[1] for file in (checkpoints / "sample").iterdir())
        assert previews == ["000004", "000006", "000008"]
        # The last interval save repeats the final save and was removed as a duplicate;
        # retention (one historical save) pruned the save at 4.
        name = train["args"][train["args"].index("--output_name") + 1]
        assert resumed["removed_duplicates"] == [f"{name}-step00000008.safetensors", f"{name}-step00000008-state"]
        service.retain_checkpoints(restored["id"])
        resumed = service.get(restored["id"])
        assert sorted(resumed["retention"]["removed"]) == [
            f"{name}-step00000004-state",
            f"{name}-step00000004.safetensors",
        ]
        assert sorted(
            path.name for path in checkpoints.iterdir() if path.name != "sample" and not path.name.startswith(".")
        ) == [
            f"{name}-state",
            f"{name}-step00000006-state",
            f"{name}-step00000006.safetensors",
            f"{name}.safetensors",
        ]

        progress = service.progress(restored["id"])
        assert progress is not None and (progress["step"], progress["total"]) == (8, 8)
        timing = json.loads(Path(resumed["directory"], "timing-train.json").read_text(encoding="utf-8"))
        assert (timing["training"]["step"], timing["previews"]["completed"]) == (8, 3)

        states = {state["name"]: state for state in service.saved_states(restored["id"])}
        assert states[f"{name}-step00000006-state"]["resume_step"] == 6
        assert states[f"{name}-state"]["resume_step"] == 8
        outputs = OutputBrowser(service).scan(restored["id"])["outputs"]
        assert sorted(
            output["metadata"]["preview"]["step"] for output in outputs if "preview" in output.get("metadata", {})
        ) == [4, 6, 8]

        # Retrying the segment resumes the same state, so it keeps the same numbering.
        service.store.update(restored["id"], lambda r: r.update(status="failed"))
        retried = service.retry(restored["id"])
        assert retried["segment"] == resumed["segment"]
        wait_for(service, retried["id"], "prepared", device_released=True)
    finally:
        service.close()

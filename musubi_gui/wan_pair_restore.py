"""Restore both expert states and the alternating schedule as a new Run segment."""

import copy
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from musubi_gui.recipe import Stage
from musubi_gui.run_paths import job_folder
from musubi_gui.run_restore import (
    ENDED_FIELDS,
    SegmentFolder,
    continuous_segment,
    resume_step,
    revision_inputs,
    revision_stages,
    segment_steps,
)
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_pair_plan import argument, paired_stages, set_argument

if TYPE_CHECKING:
    from musubi_gui.job_models import Job
    from musubi_gui.resume_refresh import Refresh


def restored_pair(
    original: dict[str, Any],
    state: dict[str, Any],
    target_steps: int,
    *,
    revision: "Job | None" = None,
    changes: list[dict[str, Any]] | None = None,
    refresh: "Callable[[Path], Refresh] | None" = None,
) -> dict[str, Any]:
    """Continue both experts until `target_steps`: per expert for per-expert Runs, schedule updates for legacy Runs.

    With `revision`, phases train and sample as that revision of the Job says, keeping the saved schedule. With
    `refresh`, which snapshots the datasets into the segment's folder, the segment prepares its own caches first.
    """
    steps = segment_steps(original, state, target_steps)
    with SegmentFolder(original, revision) as folder:
        directory = folder.path
        snapshot = refresh(directory) if refresh and revision else None
        folder.cache = snapshot.cache if snapshot else None
        sources = copy.deepcopy(state["sources"])
        if snapshot:
            files = dict(snapshot.input_files)
        else:
            files = revision_inputs(original, revision, directory) if revision else dict(original["input_files"])
        for source in sources.values():
            for name, expected in source["files"].items():
                file = str(Path(source["path"]) / name)
                if digest_file(Path(file)) != expected:
                    raise ValueError("Paired expert state changed before restoration.")
                files[file] = expected
            checkpoint = source["checkpoint"]
            if digest_file(Path(checkpoint["path"])) != checkpoint["sha256"]:
                raise ValueError("Paired checkpoint changed before restoration.")
            files[checkpoint["path"]] = checkpoint["sha256"]
        copies = []
        for expert, source in sources.items():
            checkpoint = source["checkpoint"]
            stem = f"{job_folder(original['job']['name'])}-r{original['job']['revision']}"
            target = directory / "checkpoints" / f"{stem}-{expert}-step{checkpoint['steps']:08d}.safetensors"
            copies.append((checkpoint["path"], target, checkpoint["sha256"]))
            source["checkpoint"] = {**checkpoint, "path": str(target)}
            files[str(target)] = checkpoint["sha256"]
        # A refreshed segment's templates start with its cache stages, which the schedule keeps first.
        templates = revision_stages(original, revision, directory, snapshot) if revision else []
        for stage_name in () if revision else ("train", "generate"):
            old = next((stage for stage in original["stages"] if stage["name"] == stage_name), None)
            if old:
                args = list(old["args"])
                if stage_name == "train":
                    set_argument(args, "--output_dir", str(directory / "checkpoints"))
                    stem = argument(args, "--output_name").rsplit("-", 2)[0]
                    set_argument(args, "--output_name", stem)
                else:
                    set_argument(args, "--save_path", str(directory / "samples"))
                templates.append(Stage(stage_name, args, preview_memory=copy.deepcopy(old.get("preview_memory"))))
        job = revision.model_dump() if revision else original["job"]
        models = {**original["settings"]["models"], **job["config"].get("components", {})}
        config = job["config"]
        stages = paired_stages(
            templates,
            models,
            steps,
            state["interval"],
            start=state["schedule_step"],
            sources=sources,
            preview_every=config.get("preview_every", 0),
            final_sample=config.get("sample_enabled", True),
            per_expert=any(stage.get("training", {}).get("per_expert") for stage in original["stages"]),
        )
        (directory / "checkpoints").mkdir()
        for source_path, target, expected_hash in copies:
            shutil.copyfile(source_path, target)
            if digest_file(target) != expected_hash:
                raise ValueError("Source checkpoint changed while copying the restored pair.")
        record = copy.deepcopy(original)
        record.update(
            id=uuid.uuid4().hex,
            directory=str(directory),
            status="prepared",
            phase="train",
            fingerprint=original["fingerprint"] + ":restore:" + uuid.uuid4().hex,
            created=datetime.now(UTC).isoformat(),
            execution_started=None,
            processes=[],
            error=None,
            resume_of=original["id"],
            saved_states=[],
            input_files=files,
            pair_sources=sources,
            pair_seals={},
            expert_states={},
            stages=[{**stage.record(), "status": "pending"} for stage in stages],
            segment={
                **continuous_segment(resume_step(original, state), target_steps),
                "schedule_start": state["schedule_step"],
                "optimizer_step_before": state["schedule_step"],
                **({"source_revision": original["job"]["revision"], "changes": changes or []} if revision else {}),
            },
            job=job,
        )
        if snapshot:
            record.update({**snapshot.fields(), "input_files": files}, order=time.time_ns())
            record["segment"].update(dataset_changes=snapshot.dataset)
        # A paired segment resumes from `pair_sources`, never a single `state_source`.
        for key in [*ENDED_FIELDS, "state_source"]:
            record.pop(key, None)
        return record

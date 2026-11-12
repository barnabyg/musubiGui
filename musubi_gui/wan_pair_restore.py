"""Restore both expert states and the alternating schedule as a new Run segment."""

import copy
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musubi_gui.recipe import Stage
from musubi_gui.run_paths import create_run_directory
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_pair_plan import argument, paired_stages, set_argument


def restored_pair(original: dict[str, Any], state: dict[str, Any], steps: int) -> dict[str, Any]:
    directory = create_run_directory(Path(original["directory"]).parent, original["job"]["name"], "restore")
    sources = copy.deepcopy(state["sources"])
    files = dict(original["input_files"])
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
        target = directory / "checkpoints" / f"wan-{original['job']['id']}-{expert}-source.safetensors"
        copies.append((checkpoint["path"], target, checkpoint["sha256"]))
        source["checkpoint"] = {**checkpoint, "path": str(target)}
        files[str(target)] = checkpoint["sha256"]
    templates = []
    for name in ("train", "generate"):
        old = next((stage for stage in original["stages"] if stage["name"] == name), None)
        if old:
            args = list(old["args"])
            if name == "train":
                set_argument(args, "--output_dir", str(directory / "checkpoints"))
                stem = argument(args, "--output_name").rsplit("-", 2)[0]
                set_argument(args, "--output_name", stem)
            else:
                set_argument(args, "--save_path", str(directory / "samples"))
            templates.append(Stage(name, args))
    models = {**original["settings"]["models"], **original["job"]["config"].get("components", {})}
    config = original["job"]["config"]
    stages = paired_stages(
        templates,
        models,
        steps,
        state["interval"],
        start=state["schedule_step"],
        sources=sources,
        preview_every=config.get("preview_every", 0),
        final_sample=config.get("sample_enabled", True),
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
            "additional_steps": steps,
            "schedule_start": state["schedule_step"],
            "optimizer_step_before": state["schedule_step"],
            "counter_resets": True,
            "data_position_restored": False,
        },
    )
    for key in [
        "stop_requested",
        "stop_kind",
        "finished",
        "exit_code",
        "checkpoints",
        "removed_duplicates",
        "retention",
        "sample_pair",
        "samples",
        "state_source",
        "controller",
        "job_objects",
        "launch_pending",
    ]:
        record.pop(key, None)
    return record

"""Retrying a Run that ended early: the same frozen inputs and stages, in new Run and cache folders."""

import copy
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from musubi_gui.recipe import Stage
from musubi_gui.run_paths import create_run_directory
from musubi_gui.settings import Settings
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_pair_runtime import is_paired


def retried_plan(previous: dict[str, Any], verify_inputs: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """The execution plan fields for retrying `previous`, after copying its native input files."""
    if is_paired(previous) and (
        previous.get("pair_sources")
        or any(stage["status"] != "pending" for stage in previous["stages"] if stage["name"] == "train")
    ):
        raise ValueError(
            "Inspect paired saved states and restore the schedule as a new segment. To restart both experts, prepare a new Run from the Job."
        )
    if previous["status"] not in {"failed", "cancelled", "interrupted"} or previous.get("processes"):
        raise ValueError(
            "Only failed, cancelled or interrupted Runs without recorded processes can be retried. "
            "Choose Check for leftover processes first if processes remain."
        )
    verify_inputs(previous)
    directory = create_run_directory(Path(previous["directory"]).parent, previous["job"]["name"], "retry")
    cache = create_run_directory(Path(previous["cache"]).parent, previous["job"]["name"], "retry")
    input_files = _copy_native_inputs(previous, directory, cache)
    verify_inputs(previous)
    stages = []
    for stage in previous["stages"]:
        args = [
            arg
            if arg.endswith(".py")
            else arg.replace(previous["directory"], str(directory)).replace(previous["cache"], str(cache))
            for arg in stage["args"]
        ]
        training = copy.deepcopy(stage.get("training"))
        if training:
            training["checkpoint"] = training["checkpoint"].replace(previous["directory"], str(directory))
        stages.append(
            Stage(
                stage["name"],
                args,
                training,
                stage.get("preview_step"),
                preview_memory=copy.deepcopy(stage.get("preview_memory")),
            )
        )
    return {
        "job": copy.deepcopy(previous["job"]),
        "settings": Settings.model_validate(previous["settings"]),
        "manifest": copy.deepcopy(previous["manifest"]),
        "directory": directory,
        "cache": cache,
        "stages": stages,
        "fingerprint": previous["fingerprint"] + ":retry:" + uuid.uuid4().hex,
        "stage_keys": previous.get("stage_keys", {}),
        "input_files": input_files,
        "provenance": previous.get("provenance", {}),
    }


def _copy_native_inputs(previous: dict[str, Any], directory: Path, cache: Path) -> dict[str, str]:
    """Copy the dataset config and other native inputs, pointed at the new folders; returns the sealed input files."""
    # An H3 Run has one metadata file per video section.
    metadata = sorted(file.name for file in Path(previous["directory"]).glob("dataset*.jsonl"))
    native_names = ["dataset.toml", *metadata, "samples.txt", "previews.json"]
    old_native = {str(Path(previous["directory"]) / name) for name in native_names}
    input_files = {path: value for path, value in previous.get("input_files", {}).items() if path not in old_native}
    for name in native_names:
        source = Path(previous["directory"]) / name
        if source.is_file():
            text = source.read_text(encoding="utf-8")
            if name == "dataset.toml":
                text = text.replace(previous["cache"].replace("\\", "/"), cache.as_posix())
                for file in metadata:
                    text = text.replace(
                        json.dumps((Path(previous["directory"]) / file).as_posix(), ensure_ascii=False),
                        json.dumps((directory / file).as_posix(), ensure_ascii=False),
                    )
            target = directory / name
            target.write_text(text, encoding="utf-8")
            input_files[str(target)] = digest_file(target)
    return input_files

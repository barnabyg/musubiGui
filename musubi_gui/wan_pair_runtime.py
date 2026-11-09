"""Validate phase outputs and keep paired checkpoints/state tied to their training schedule."""

import hashlib
import re
from pathlib import Path
from typing import Any

from musubi_gui.safetensors_file import complete_safetensors
from musubi_gui.saved_state import inspect_state
from musubi_gui.settings import within_root
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_pair import PAIRED_RECIPE, expert_steps
from musubi_gui.wan_pair_plan import argument


def is_paired(run: dict[str, Any]) -> bool:
    return bool(run["job"].get("family") == "wan" and run["job"].get("config", {}).get("recipe") == PAIRED_RECIPE)


def stage_log(stage: dict[str, Any]) -> str:
    phase = stage.get("training")
    return f"train-{phase['expert']}-{phase['start']:08d}.log" if phase else f"{stage['name']}.log"


def sealed_state(run: dict[str, Any], directory: Path, expected: int) -> dict[str, Any]:
    if not run["fixture"]:
        scaler = within_root(directory / "scaler.pt", [directory])
        if not scaler.is_file() or not scaler.stat().st_size:
            raise ValueError("Incomplete FP16 expert state: the gradient scaler is required for continuation.")
    previous = run.get("pair_seals", {}).get(str(directory))
    state = inspect_state(run, directory, previous)
    if not state["valid"]:
        raise ValueError(state["error"])
    if state["optimizer_step"] != expected:
        raise ValueError(f"Expert state has optimizer step {state['optimizer_step']}; expected {expected}.")
    # Include scaler and every RNG/custom state, not only the four required files.
    files = {
        file.relative_to(directory).as_posix(): digest_file(within_root(file, [directory]))
        for file in directory.rglob("*")
        if file.is_file()
    }
    if previous and files != previous["files"]:
        raise ValueError("Expert saved state changed after it was recorded.")
    return {**state, "files": files}


def validate_phase_input(run: dict[str, Any], stage: dict[str, Any]) -> None:
    phase = stage["training"]
    args = stage["args"]
    before = phase[f"{phase['expert']}_before"]
    if before:
        if "--resume" not in args:
            raise ValueError("The expert's previous training state is missing; refusing to restart it.")
        path = argument(args, "--resume")
        # Restored segments have already frozen and checked all source state files as inputs.
        if Path(path).is_relative_to(Path(run["directory"])):
            if path not in run.get("pair_seals", {}):
                raise ValueError("The previous expert phase has no validated saved state.")
            sealed_state(run, Path(path), before)


def finish_phase(run: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    phase = stage["training"]
    expected = phase[f"{phase['expert']}_before"] + phase["steps"]
    checkpoint = Path(phase["checkpoint"])
    complete_safetensors(checkpoint)
    directory = Path(argument(stage["args"], "--output_dir")) / (argument(stage["args"], "--output_name") + "-state")
    state = sealed_state(run, directory, expected)
    state["checkpoint"] = {"path": str(checkpoint), "steps": expected, "sha256": digest_file(checkpoint)}
    return state


def paired_saved_states(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair each available phase/interval save with the other expert's most recent prior state."""
    previous = {entry["id"]: entry for entry in run.get("saved_states", [])}
    latest = dict(run.get("pair_sources", {}))
    results = []
    for stage in run["stages"]:
        phase = stage.get("training")
        if not phase:
            continue
        output = Path(argument(stage["args"], "--output_dir"))
        name = argument(stage["args"], "--output_name")
        candidates: list[tuple[int, Path]] = []
        for directory in output.glob(f"{name}*-state"):
            suffix = directory.name.removeprefix(name)
            match = re.fullmatch(r"-step(\d+)-state", suffix)
            if suffix == "-state":
                candidates.append((phase["steps"], directory))
            elif match and 0 < int(match[1]) <= phase["steps"]:
                candidates.append((int(match[1]), directory))
        for steps, directory in sorted(candidates):
            identity = hashlib.sha256(str(directory).encode()).hexdigest()[:24]
            entry: dict[str, Any] = {
                "id": identity,
                "name": f"Paired schedule step {phase['start'] + steps}",
                "valid": False,
                "error": "",
                "paired": True,
                "schedule_step": phase["start"] + steps,
                "optimizer_step": phase["start"] + steps,
                "interval": phase["interval"],
            }
            try:
                expected = phase[f"{phase['expert']}_before"] + steps
                state = sealed_state(run, directory, expected)
                checkpoint = directory.with_name(directory.name.removesuffix("-state") + ".safetensors")
                complete_safetensors(checkpoint)
                state["checkpoint"] = {"path": str(checkpoint), "steps": expected, "sha256": digest_file(checkpoint)}
                old = previous.get(identity)
                known = (old or {}).get("sources", {}).get(phase["expert"])
                if known and (known["files"] != state["files"] or known["checkpoint"] != state["checkpoint"]):
                    raise ValueError("Expert saved state changed after it was recorded.")
                # A later complete state is self-contained. A missing older counterpart must
                # not prevent reconstructing a later complete pair from two valid states.
                latest = {**latest, phase["expert"]: state}
                sources = dict(latest)
                counts = expert_steps(entry["schedule_step"], phase["interval"])
                for expert, count in counts.items():
                    if count and (expert not in sources or sources[expert]["optimizer_step"] != count):
                        raise ValueError("The other expert's matching saved state is missing or invalid.")
                    if expert in sources:
                        source = sources[expert]
                        for path, expected_hash in source["files"].items():
                            if digest_file(Path(source["path"]) / path) != expected_hash:
                                raise ValueError("A paired expert's saved state changed after it was recorded.")
                entry.update(valid=True, sources=sources, counts=counts)
                if old and old.get("sources") != sources:
                    raise ValueError("Paired saved state changed after it was recorded.")
            except (OSError, ValueError) as error:
                entry.update(valid=False, error=str(error))
            results.append(entry)
    found = {entry["id"] for entry in results}
    results.extend(
        {**entry, "valid": False, "error": "Paired saved state is missing or moved."}
        for identity, entry in previous.items()
        if identity not in found
    )
    return results


def sampling_pair(run: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for expert, flag in [("low", "--lora_weight"), ("high", "--lora_weight_high_noise")]:
        path = argument(stage["args"], flag)
        state = run.get("expert_states", {}).get(expert) or run.get("pair_sources", {}).get(expert)
        checkpoint = state.get("checkpoint") if state else None
        if not checkpoint or checkpoint["path"] != path or digest_file(Path(path)) != checkpoint["sha256"]:
            raise ValueError(f"The trained {expert}-noise checkpoint is missing or changed; paired sampling stopped.")
        result[expert] = checkpoint
    return result

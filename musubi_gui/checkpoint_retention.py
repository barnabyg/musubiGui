"""Bound checkpoint history without pruning incomplete saves or restoration inputs."""

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from musubi_gui.run_store import references_folder
from musubi_gui.safetensors_file import complete_safetensors
from musubi_gui.saved_state import inspect_state
from musubi_gui.settings import atomic_json, within_root
from musubi_gui.snapshots import digest_file
from musubi_gui.wan_pair import expert_steps
from musubi_gui.wan_pair_runtime import is_paired


@dataclass
class Save:
    checkpoint: Path
    state: Path | None
    expert: str
    step: int
    optimizer_step: int


def _saves(run: dict[str, Any], validated: dict[str, tuple[object, Save]]) -> list[Save]:
    """Only complete, named recipe saves enter retention; unknown files are preserved."""
    saves = []
    root = Path(run["directory"]) / "checkpoints"
    for stage in run["stages"]:
        args = stage.get("args", [])
        if stage["name"] != "train" or "--output_name" not in args:
            continue
        name = args[args.index("--output_name") + 1]
        phase = stage.get("training", {})
        expert = phase.get("expert", "model")
        total = int(args[args.index("--max_train_steps") + 1])
        for checkpoint in root.glob(f"{name}*.safetensors"):
            suffix = checkpoint.stem.removeprefix(name)
            match = re.fullmatch(r"-step(\d+)", suffix)
            if suffix == "":
                step = total
            elif match and 0 < int(match[1]) <= total:
                step = int(match[1])
            else:
                continue
            try:
                within_root(checkpoint, [root])
                if checkpoint.is_symlink():
                    continue
                state_path = checkpoint.with_name(checkpoint.stem + "-state")
                if state_path.is_symlink() or state_path.is_junction():
                    continue
                files = [checkpoint, *sorted(state_path.rglob("*"))] if state_path.is_dir() else [checkpoint]
                stamp = tuple(
                    (str(file), file.stat().st_mtime_ns, file.stat().st_size) for file in files if file.is_file()
                )
                known = validated.get(str(checkpoint))
                if known and known[0] == stamp:
                    saves.append(known[1])
                    continue
                complete_safetensors(checkpoint)
                state: Path | None = None
                optimizer_step = step
                if run["job"]["config"].get("save_state"):
                    if is_paired(run) and not run["fixture"] and not (state_path / "scaler.pt").is_file():
                        continue
                    previous = run.get("pair_seals", {}).get(str(state_path))
                    inspected = inspect_state(run, state_path, previous)
                    if not inspected["valid"]:
                        continue
                    complete_safetensors(state_path / "model.safetensors")
                    if any(digest_file(state_path / file) != expected for file, expected in inspected["files"].items()):
                        continue
                    state = state_path
                    optimizer_step = inspected["optimizer_step"]
                save = Save(checkpoint, state, expert, phase.get("start", 0) + step, optimizer_step)
                validated[str(checkpoint)] = (stamp, save)
                saves.append(save)
            except (OSError, ValueError):
                # The trainer may still be writing. Such a save never authorizes deletion.
                continue
    return saves


def retention_candidates(run: dict[str, Any], validated: dict[str, tuple[object, Save]]) -> tuple[list[Save], set[str]]:
    """Validate outside the RunStore transaction, then recheck protected references inside it."""
    limit = run["job"].get("config", {}).get("keep_checkpoints", 0)
    if limit <= 0:
        return [], set()
    saves = _saves(run, validated)
    keep: set[str] = set()
    for expert in {save.expert for save in saves}:
        ordered = [save for save in saves if save.expert == expert]
        # One newest/final save plus N historical saves. A newer valid save establishes a safe boundary.
        steps = sorted({save.step for save in ordered})[-(limit + 1) :]
        keep.update(str(save.checkpoint) for save in ordered if save.step in steps)
    protected: set[str] = set()
    if is_paired(run):
        interval = run["job"]["config"]["expert_interval"]
        for save in saves:
            if str(save.checkpoint) not in keep:
                continue
            for expert, count in expert_steps(save.step, interval).items():
                if not count:
                    continue
                matches = [
                    candidate
                    for candidate in saves
                    if candidate.expert == expert and candidate.optimizer_step == count and candidate.step <= save.step
                ]
                if matches:
                    other = max(matches, key=lambda candidate: candidate.step)
                    protected.update((str(other.checkpoint), str(other.state)))
                elif run.get("pair_sources", {}).get(expert, {}).get("optimizer_step") != count:
                    # No proven counterpart: preserve history instead of destroying a possible recovery path.
                    return [], set()
    return [save for save in saves if str(save.checkpoint) not in keep], protected


def apply_retention(
    run: dict[str, Any], others: list[dict[str, Any]], candidates: list[Save], protected: set[str]
) -> None:
    """Run under the record transaction so a newly published restoration cannot race pruning.

    A durable journal precedes renaming; hidden quarantined saves cannot be offered for restore.
    Failed removal is retried at the next boundary and is visible in the Run's retention status.
    """
    root = Path(run["directory"]) / "checkpoints"
    if root.is_symlink() or root.is_junction():
        raise ValueError("Checkpoint retention refuses a linked checkpoint folder.")
    root = within_root(root, [Path(run["directory"])])
    journal_file = Path(run["directory"]) / "retention.json"
    journal: dict[str, Any] = (
        json.loads(journal_file.read_text(encoding="utf-8"))
        if journal_file.exists()
        else {
            "version": 1,
            "removed": [],
            "pending": [],
            "errors": [],
            "protected": [],
        }
    )
    if journal.get("version") != 1:
        raise ValueError("Unsupported retention journal; preserve it before repair.")
    protected_paths = set(protected)
    protected_paths.update(state["path"] for state in run.get("expert_states", {}).values())
    protected_paths.update(state["checkpoint"]["path"] for state in run.get("expert_states", {}).values())
    pending_stages = [stage for stage in run["stages"] if stage["status"] == "pending"]
    own_inputs = {
        "stages": pending_stages,
        "input_files": run.get("input_files", {}),
        "state_source": run.get("state_source"),
    }
    blocked: list[str] = []
    for save in candidates:
        paths = [save.checkpoint, *([save.state] if save.state else [])]
        if any(
            str(path) in protected_paths
            or references_folder(own_inputs, str(path))
            or any(references_folder(other, str(path)) for other in others)
            for path in paths
        ):
            blocked.extend(path.name for path in paths)
            continue
        for path in paths:
            if path.name not in journal["pending"] and path.name not in journal["removed"]:
                journal["pending"].append(path.name)
    journal.update(
        errors=[],
        protected=blocked,
        counterpart_states=[save.state.name for save in candidates if save.state and str(save.state) in protected],
    )
    atomic_json(journal_file, journal)
    quarantine = root / ".retention"
    if quarantine.is_symlink() or quarantine.is_junction():
        raise ValueError("Checkpoint retention refuses linked quarantine storage.")
    quarantine.mkdir(exist_ok=True)
    for name in list(journal["pending"]):
        if not isinstance(name, str) or Path(name).name != name or name.startswith("."):
            raise ValueError("Invalid retention journal path; nothing outside checkpoints may be removed.")
        source = root / name
        target = quarantine / name
        try:
            if source.exists() and (
                str(source) in protected_paths
                or references_folder(own_inputs, str(source))
                or any(references_folder(other, str(source)) for other in others)
            ):
                journal["pending"].remove(name)
                journal["protected"].append(name)
                atomic_json(journal_file, journal)
                continue
            within_root(source, [root])
            within_root(target, [quarantine])
            if source.is_symlink() or source.is_junction() or target.is_symlink() or target.is_junction():
                raise ValueError("Retention refuses linked saved artifacts.")
            if source.exists():
                if target.exists():
                    raise ValueError("Retention quarantine already contains a different save.")
                source.rename(target)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            journal["pending"].remove(name)
            journal["removed"].append(name)
        except (OSError, ValueError) as error:
            journal["errors"].append(f"Could not finish pruning {name}: {error}")
        atomic_json(journal_file, journal)
    run["retention"] = journal
    # Intentional removals must not masquerade as damaged checkpoints or missing saved states.
    removed = set(journal["removed"]) | set(journal["pending"])
    run["checkpoints"] = [path for path in run.get("checkpoints", []) if Path(path).name not in removed]
    run["saved_states"] = retained_states(run, run.get("saved_states", []))


def retained_states(run: dict[str, Any], states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    removed = set(run.get("retention", {}).get("removed", [])) | set(run.get("retention", {}).get("pending", []))
    root = Path(run["directory"]) / "checkpoints"
    identities = {
        hashlib.sha256((str(root / name) if is_paired(run) else name).encode()).hexdigest()[:24]
        for name in removed
        if name.endswith("-state")
    }
    counterpart_ids = {
        hashlib.sha256(str(root / name).encode()).hexdigest()[:24]
        for name in run.get("retention", {}).get("counterpart_states", [])
    }
    return [
        entry
        for entry in states
        if entry["id"] not in identities
        and not (
            entry["id"] in counterpart_ids
            and entry.get("error") == "The other expert's matching saved state is missing or invalid."
        )
        and not any(
            Path(source.get("path", "")).name in removed for source in entry.get("sources", {"model": entry}).values()
        )
    ]

"""Checking and recording what a successful training or sampling stage produced."""

from pathlib import Path
from typing import Any

from musubi_gui.checkpoint_dedup import remove_final_duplicates
from musubi_gui.media_probe import inspect as inspect_media
from musubi_gui.run_store import RunStore
from musubi_gui.snapshots import digest_file
from musubi_gui.step_offset import segment_step_offset
from musubi_gui.wan_pair_runtime import finish_phase, stage_log

SAMPLE_SUFFIXES = {".mp4", ".mov", ".webm", ".wav", ".png", ".jpg"}


def record_checkpoints(store: RunStore, run: dict[str, Any], stage: dict[str, Any]) -> None:
    """Validate a finished training stage's previews and saves, and record its checkpoints and expert state."""
    from musubi_gui.preview import validate_previews

    run_id = run["id"]
    validate_previews(stage["args"], run["job"]["family"], segment_step_offset(run, stage))
    removed = remove_final_duplicate_saves(run, stage)
    checkpoints = list((Path(run["directory"]) / "checkpoints").glob("*.safetensors"))
    if not any(file.stat().st_size > 0 for file in checkpoints):
        raise ValueError("Training exited successfully but no nonempty checkpoint was found.")
    store.update(
        run_id,
        lambda r: r.update(
            checkpoints=[str(file) for file in checkpoints],
            removed_duplicates=[*r.get("removed_duplicates", []), *removed],
        ),
    )
    if stage.get("training"):
        state = finish_phase(store.get(run_id), stage)

        def save_expert(record: dict[str, Any]) -> None:
            record.setdefault("pair_seals", {})[state["path"]] = state
            record.setdefault("expert_states", {})[stage["training"]["expert"]] = state

        store.update(run_id, save_expert)


def record_samples(store: RunStore, run: dict[str, Any], stage: dict[str, Any], pair: dict[str, Any] | None) -> None:
    """Inspect a finished sampling stage's media and record each sample, replacing any at the same path."""
    destination = Path(stage["args"][stage["args"].index("--save_path") + 1])
    samples = list(destination.rglob("*")) if destination.is_dir() else [destination]
    media_files = [file for file in samples if file.is_file() and file.suffix.lower() in SAMPLE_SUFFIXES]
    if not media_files:
        raise ValueError("Sampling exited successfully but no sample was found.")
    inspected = []
    for file in media_files:
        metadata = inspect_media(file, timeline=True)
        if run["job"]["family"] == "h3" and (not metadata.get("frames") or not metadata.get("audio")):
            raise ValueError("H3 sample is incomplete: decodable video and audio are both required.")
        inspected.append(
            {
                "path": str(file),
                "status": "playable",
                "metadata": metadata,
                "sha256": digest_file(file),
                **({"lora_pair": pair} if pair else {}),
                **({"preview_step": stage["preview_step"]} if stage.get("preview_step") is not None else {}),
            }
        )
    paths = {sample["path"] for sample in inspected}
    store.update(
        run["id"],
        lambda r: r.update(
            samples=[sample for sample in r.get("samples", []) if sample["path"] not in paths] + inspected
        ),
    )


def remove_final_duplicate_saves(run: dict[str, Any], stage: dict[str, Any]) -> list[str]:
    """Keep one copy of the final save when the step count is a multiple of the checkpoint interval."""
    args = stage["args"]
    if "--output_name" not in args or "--max_train_steps" not in args:
        return []
    log_file = Path(run["directory"]) / stage_log(stage)
    removed, problems = remove_final_duplicates(
        Path(run["directory"]) / "checkpoints",
        args[args.index("--output_name") + 1],
        segment_step_offset(run, stage) + int(args[args.index("--max_train_steps") + 1]),
    )
    lines = [f"Removed duplicate of the final save: {name}" for name in removed] + problems
    if lines:
        with log_file.open("a", encoding="utf-8") as log:
            log.writelines(f"{line}\n" for line in lines)
    return removed

"""Deterministic CPU subprocesses used only by explicitly selected acceptance fixtures."""

import argparse
import json
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any


def tensor(file: Path, **metadata: str) -> None:
    """Write a tiny, labelled tensor container; it is never a usable model."""
    header = json.dumps(
        {
            "__metadata__": {"purpose": "SYNTHETIC ACCEPTANCE FIXTURE, NOT A TRAINED MODEL", **metadata},
            "fixture.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
        }
    ).encode("utf-8")
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(len(header).to_bytes(8, "little") + header + bytes(4))


def main(stage: str | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    if stage is None:
        parser.add_argument("stage", choices=["latent_cache", "text_cache", "train", "generate"])
    parser.add_argument("--dataset_config", type=Path)
    parser.add_argument("--task", default="t2v-A14B")
    parser.add_argument("--uncond_output", type=Path)
    parser.add_argument("--output_dir", type=Path)
    parser.add_argument("--output_name", default="fixture")
    parser.add_argument("--max_train_steps", type=int, default=2)
    parser.add_argument("--save_every_n_steps", type=int)
    parser.add_argument("--save_state", action="store_true")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--save_path", type=Path)
    args, _ = parser.parse_known_args()
    stage = stage or str(args.stage)
    marker = Path.cwd() / ".musubi-gui-fixture.json"
    try:
        valid = json.loads(marker.read_text(encoding="utf-8")) == {"version": 1, "kind": "synthetic-worker"}
    except (OSError, ValueError):
        valid = False
    if not valid:
        print("Refusing to run without the explicit synthetic checkout marker.")
        raise SystemExit(2)
    print(f"SYNTHETIC ACCEPTANCE FIXTURE: {stage}; no GPU or model is used.", flush=True)
    if (Path.cwd() / f"fail-{stage}").exists():
        print("Deliberate fixture failure: remove the named fail marker and retry.", flush=True)
        raise SystemExit(3)
    if stage in {"latent_cache", "text_cache"}:
        if args.dataset_config is None:
            parser.error("--dataset_config is required for cache fixtures")
        data: dict[str, Any] = tomllib.loads(args.dataset_config.read_text(encoding="utf-8"))
        architecture = "mmh3" if args.task == "t2va" else "wan"
        for dataset in data.get("datasets", []):
            media_root = Path(dataset.get("video_directory", dataset.get("image_directory", "")))
            media_files = (
                [
                    Path(json.loads(line)["video_path"])
                    for line in Path(dataset["video_jsonl_file"]).read_text(encoding="utf-8").splitlines()
                ]
                if "video_jsonl_file" in dataset
                else list(media_root.iterdir())
            )
            for media in media_files:
                if media.suffix.lower() not in {
                    ".mp4",
                    ".mov",
                    ".mkv",
                    ".webm",
                    ".avi",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                }:
                    continue
                suffix = f"0256x0256_{architecture}" if stage == "latent_cache" else f"{architecture}_te"
                tensor(Path(dataset["cache_directory"]) / f"{media.stem}_{suffix}.safetensors")
        if args.uncond_output:
            tensor(args.uncond_output)
        if stage == "latent_cache":
            print("supervised_audio_fraction=1.000000 (synthetic orchestration fixture)", flush=True)
        time.sleep(0.3)
    elif stage == "train":
        if args.output_dir is None:
            parser.error("--output_dir is required for training fixtures")
        if Path(args.output_name).name != args.output_name or args.output_name in {"", ".", ".."}:
            parser.error("--output_name must be a simple filename")
        previous = 0
        if args.resume:
            saved = json.loads((args.resume / "trainer_state.json").read_text(encoding="utf-8"))
            if not saved.get("synthetic"):
                parser.error("fixture resumes accept only synthetic saved state")
            previous = int(saved["optimizer_step"])

        def save_state(state: Path, step: int) -> None:
            state.mkdir(parents=True, exist_ok=True)
            (state / "trainer_state.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "synthetic": True,
                        "global_step": step,
                        "optimizer_step": previous + step,
                        "segment_steps": step,
                    }
                ),
                encoding="utf-8",
            )
            tensor(state / "model.safetensors")
            tensor(state / "optimizer.bin")
            tensor(state / "random_states_0.pkl")
            (state / "scheduler.bin").write_text("synthetic scheduler", encoding="utf-8")

        for step in range(1, args.max_train_steps + 1):
            time.sleep(0.5)
            print(
                f"steps: {step}/{args.max_train_steps} loss=0.0 (synthetic optimizer_step={previous + step})",
                flush=True,
            )
            # Like upstream, interval saves stamp their own metadata and include the last step.
            if args.save_every_n_steps and step % args.save_every_n_steps == 0:
                name = f"{args.output_name}-step{step:08d}"
                tensor(args.output_dir / f"{name}.safetensors", saved=f"step {step}")
                if args.save_state:
                    save_state(args.output_dir / f"{name}-state", step)
        tensor(args.output_dir / f"{args.output_name}.safetensors", saved="train end")
        if args.save_state:
            save_state(args.output_dir / f"{args.output_name}-state", args.max_train_steps)
    elif stage == "generate":
        if args.save_path is None:
            parser.error("--save_path is required for sample fixtures")
        destination = args.save_path
        if destination.suffix != ".mp4":
            destination /= "sample.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        sample = "sample.mp4" if args.task == "t2va" else "sample-wan.mp4"
        shutil.copyfile(Path.cwd() / sample, destination)
    print(f"SYNTHETIC {stage} complete", flush=True)


if __name__ == "__main__":
    main()

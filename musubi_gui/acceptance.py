"""Prepare an isolated acceptance workspace and launch the local application."""

import argparse
import io
import json
import math
import shutil
import struct
import subprocess  # nosec B404
import sys
import threading
import wave
import webbrowser
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from musubi_gui.acceptance_worker import tensor
from musubi_gui.settings import Settings, atomic_json, save_settings, validate_paths


def synthetic_video(file: Path, frames: int, audio: bool = False, rate: Fraction | int = 24) -> None:
    import av

    file.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(file), "w") as container:
        video: Any = container.add_stream("libx264", rate=rate)
        video.width, video.height, video.pix_fmt = 256, 256, "yuv420p"
        video.options = {"preset": "ultrafast", "crf": "24"}
        sound: Any = container.add_stream("aac", rate=48000) if audio else None
        if sound:
            sound.layout = "mono"
        for index in range(frames):
            frame = av.VideoFrame(256, 256, "yuv420p")
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes([48 + index // 4 if plane_index == 0 else 128]) * plane.buffer_size)
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if sound:
            total = round(frames * 48000 / rate)
            for start in range(0, total, 1024):
                count = min(1024, total - start)
                audio_frame = av.AudioFrame(format="fltp", layout="mono", samples=count)
                audio_frame.sample_rate = 48000
                audio_frame.time_base = Fraction(1, 48000)
                audio_frame.pts = start
                audio_frame.planes[0].update(
                    struct.pack(
                        f"<{count}f",
                        *(0.1 * math.sin(2 * math.pi * 440 * t / 48000) for t in range(start, start + count)),
                    )
                )
                for packet in sound.encode(audio_frame):
                    container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)


def synthetic_audio(file: Path, frames: int) -> None:
    with wave.open(str(file), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(
            b"".join(
                struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * t / 48000))) for t in range(frames * 2000)
            )
        )


def synthetic_checkout(root: Path, sample: Path) -> dict[str, str]:
    checkout, models = root / "checkout", root / "models"
    checkout.mkdir()
    atomic_json(checkout / ".musubi-gui-fixture.json", {"version": 1, "kind": "synthetic-worker"})
    package_root = str(Path(__file__).resolve().parent.parent)
    for family, prefix in [("wan", "wan"), ("h3", "minimax_h3")]:
        for suffix, stage in [
            ("cache_latents", "latent_cache"),
            ("cache_text_encoder_outputs", "text_cache"),
            ("train_network", "train"),
            ("generate_video", "generate"),
        ]:
            (checkout / f"{prefix}_{suffix}.py").write_text(
                f"import sys\nsys.path.insert(0, {package_root!r})\nfrom musubi_gui.acceptance_worker import main\nmain({stage!r})\n",
                encoding="utf-8",
            )
    accelerate = checkout / "accelerate" / "commands"
    accelerate.mkdir(parents=True)
    (accelerate.parent / "__init__.py").write_text(
        "# Synthetic acceptance shim; not Hugging Face Accelerate.\n", encoding="utf-8"
    )
    (accelerate / "__init__.py").write_text("", encoding="utf-8")
    (accelerate / "launch.py").write_text(
        "import runpy\nimport sys\nfrom pathlib import Path\n"
        "index = next(i for i, arg in enumerate(sys.argv[1:], 1) if arg.endswith('.py') and Path(arg).is_file())\n"
        "sys.argv = sys.argv[index:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n",
        encoding="utf-8",
    )
    shutil.copyfile(sample, checkout / "sample.mp4")
    shutil.copyfile(root / "datasets" / "wan" / "motion.mp4", checkout / "sample-wan.mp4")
    git = shutil.which("git")
    if not git:
        raise ValueError("Git is required to identify the synthetic checkout with its own real revision.")
    # B603: fixed local fixture Git commands, no shell, no remote or inherited hooks.
    for arguments in [
        ["init", "--quiet", str(checkout)],
        ["-C", str(checkout), "-c", f"core.hooksPath={root / 'empty-hooks'}", "add", "--all"],
        [
            "-C",
            str(checkout),
            "-c",
            "user.name=Synthetic acceptance fixture",
            "-c",
            "user.email=fixture@localhost",
            "-c",
            f"core.hooksPath={root / 'empty-hooks'}",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "Synthetic CPU fixture; no upstream code or model weights",
        ],
    ]:
        subprocess.run([git, *arguments], check=True, capture_output=True, text=True)  # nosec B603
    components = {}
    for name in ["wan_low", "wan_high", "wan_vae", "wan_t5", "h3_dit", "h3_text", "h3_video_vae", "h3_audio_vae"]:
        file = models / f"{name}.safetensors"
        tensor(file)
        components[name] = str(file)
    return components


def prepare(
    workspace: Path,
    mode: str,
    selected: Path | None,
    *,
    scan_library: bool = True,
    wan_paired: bool = False,
    preview_images: bool = False,
    dataset_media: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    if workspace.exists():
        raise ValueError("Choose a new, nonexistent directory; acceptance never overwrites an existing workspace.")
    settings = Settings()
    if mode == "gpu":
        if selected is None:
            raise ValueError(
                "GPU acceptance requires --settings pointing to your existing saved installation and model paths."
            )
        settings = Settings.model_validate_json(selected.read_text(encoding="utf-8"))
        if settings.version != 1:
            raise ValueError("Unsupported settings format; the source file has been preserved.")
        validate_paths(settings)
        if not settings.checkout or not settings.python:
            raise ValueError("Select the existing training checkout and Python before GPU acceptance.")
    workspace.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "status": "preparing",
        "gpu_evidence": False,
        "jobs": [],
        "workspace": str(workspace),
        "state": str(workspace / "state"),
        "notice": "Synthetic CPU execution is not model-support evidence."
        if mode == "fixture"
        else "GPU work starts only when explicitly requested in the GUI; inspect each resulting Run.",
    }
    atomic_json(workspace / "acceptance.json", manifest)
    try:
        from musubi_gui.dataset_fixture import prepare as prepare_library
        from musubi_gui.datasets import DatasetStore
        from musubi_gui.jobs import JobDataset, JobDraft, JobStore, smoke_config

        prepare_library(workspace / "library")
        datasets = workspace / "datasets"
        families: tuple[Literal["wan", "h3"], ...] = ("wan", "h3")
        for family in families:
            folder = datasets / family
            folder.mkdir(parents=True)
            # Each family's native training rate, so no frames are dropped by resampling.
            frames, rate = (17, 16) if family == "wan" else (124, 24)
            synthetic_video(folder / "motion.mp4", frames, rate=rate)
            (folder / "motion.txt").write_text(
                "A blue field gradually brightens. A steady tone plays.", encoding="utf-8"
            )
            if family == "h3":
                synthetic_audio(folder / "motion.wav", frames)
        if preview_images or dataset_media:
            from PIL import Image, ImageDraw

            for family in families:
                folder = datasets / f"{family}-images"
                folder.mkdir()
                for index in range(15 if preview_images else 2):
                    image = Image.new("RGB", (256, 256), "blue")
                    ImageDraw.Draw(image).rectangle((index * 10, 80, index * 10 + 48, 128), fill="red")
                    image.save(folder / f"image{index:02d}.png")
                    (folder / f"image{index:02d}.txt").write_text(
                        "A red square on a blue background. No sound.", encoding="utf-8"
                    )
        sample = workspace / "sample.mp4"
        synthetic_video(sample, 124, audio=True)
        if mode == "fixture":
            components = synthetic_checkout(workspace, sample)
            settings = Settings(
                checkout=str(workspace / "checkout"),
                python=sys.executable,
                model_roots=[str(workspace / "models")],
                models=components,
            )
        settings = settings.model_copy(
            update={
                "output_root": str(workspace / "outputs"),
                "cache_root": str(workspace / "cache"),
                "dataset_roots": [str(datasets), str(workspace / "library" / "media 资料")],
            }
        )
        validate_paths(settings)
        save_settings(workspace / "state", settings)
        store, jobs = DatasetStore(workspace / "state"), JobStore(workspace / "state")
        for family in families:
            dataset = store.register(
                f"{family.upper()} acceptance", str(datasets / (f"{family}-images" if preview_images else family))
            )
            store.scan(dataset["id"])
            for number in [1, 2]:
                config = smoke_config(family).model_copy(update={"save_state": True})
                if preview_images:
                    config = config.model_copy(update={"steps": 500, "preview_every": 100, "save_every": 100})
                if wan_paired and family == "wan":
                    config = config.model_copy(
                        update={
                            "recipe": "alternating",
                            "steps": 500 if preview_images else 4,
                            "expert_interval": 30 if preview_images else 1,
                        }
                    )
                job = jobs.create(
                    JobDraft(
                        name=f"{family.upper()} acceptance {number}",
                        family=family,
                        datasets=[JobDataset(dataset_id=dataset["id"])],
                        config=config,
                    )
                )
                manifest["jobs"].append({"id": job.id, "name": job.name, "family": family, "dataset_id": dataset["id"]})
            if dataset_media:
                images = store.register(f"{family.upper()} images", str(datasets / f"{family}-images"))
                store.scan(images["id"])
                for combined in [False, True]:
                    rows = [JobDataset(dataset_id=images["id"], repeats=2)]
                    if combined:
                        rows.append(JobDataset(dataset_id=dataset["id"]))
                    job = jobs.create(
                        JobDraft(
                            name=f"{family.upper()} images" + (" + videos" if combined else " only"),
                            family=family,
                            datasets=rows,
                            config=smoke_config(family),
                        )
                    )
                    manifest["jobs"].append(
                        {"id": job.id, "name": job.name, "family": family, "dataset_id": images["id"]}
                    )
        library = store.register("Mixed media and invalid item", str(workspace / "library" / "media 资料"))
        if scan_library:
            store.scan(library["id"])
        manifest["library_dataset"] = library["id"]
        manifest["status"] = "ready"
    except Exception:
        manifest["status"] = "failed"
        atomic_json(workspace / "acceptance.json", manifest)
        raise
    atomic_json(workspace / "acceptance.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--mode", choices=["fixture", "gpu"], default="fixture")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-library-scan", action="store_true")
    parser.add_argument(
        "--wan-paired",
        action="store_true",
        help="Prepare four Wan steps: low, high, low, high, with state saving and a paired sample.",
    )
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--dataset-media", action="store_true", help="Add image-only and image-plus-video jobs for both families."
    )
    parser.add_argument(
        "--preview-images",
        action="store_true",
        help="15 synthetic captioned images, 500 steps and previews every 100 steps.",
    )
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        manifest = prepare(
            args.workspace,
            args.mode,
            args.settings,
            scan_library=not args.skip_library_scan,
            wan_paired=args.wan_paired,
            preview_images=args.preview_images,
            dataset_media=args.dataset_media,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Acceptance preparation failed: {error}")
        raise SystemExit(2) from error
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    if not args.prepare_only:
        import uvicorn

        from musubi_gui.app import create_app

        if not args.no_browser:
            timer = threading.Timer(1.5, webbrowser.open, args=[f"http://127.0.0.1:{args.port}"])
            timer.daemon = True
            timer.start()
        uvicorn.run(
            create_app(Path(manifest["state"]), fixture_mode=args.mode == "fixture"),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
        )


if __name__ == "__main__":
    main()

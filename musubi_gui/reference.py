"""Pinned, measured GPU reference scenario. Never represents a plan as GPU proof."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess  # nosec B404
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.diagnostics import training_environment, upstream_revision
from musubi_gui.h3 import h3_stages, validate_h3
from musubi_gui.recipe import UPSTREAM, Stage, python_args, reference_environment
from musubi_gui.redaction import redact
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import Settings, atomic_json, validate_paths, within_root
from musubi_gui.wan import validate_wan, wan_stages


def stop_tree(process: subprocess.Popen[str]) -> None:
    try:
        tree = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        tree = []
    for child in reversed(tree):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)
    psutil.wait_procs(tree, timeout=15)


def stage_run(stage: Stage, settings: Settings, run: Path, cache: Path, timeout: int) -> dict[str, Any]:
    env = reference_environment(cache)
    start = time.monotonic()
    free_before = shutil.disk_usage(cache).free
    # B603: stages are fixed CLI argument arrays; only paths come from validated local settings.
    process = subprocess.Popen(
        stage.args,
        cwd=settings.checkout,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )  # nosec B603
    peak_rss = peak_host = 0
    peak_vram: int | None = None
    monitor_errors = 0
    termination = None
    log_path = run / f"{stage.name}.log"

    def drain() -> None:
        with log_path.open("w", encoding="utf-8") as stream:
            if process.stdout:
                for line in process.stdout:
                    stream.write(redact(line))
                    stream.flush()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    smi = shutil.which("nvidia-smi")
    try:
        while process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                rss = sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)] if p.is_running())
                peak_rss = max(peak_rss, rss)
                peak_host = max(peak_host, psutil.virtual_memory().used)
                if rss > 60 * 1024**3:
                    termination = "Sampled process-tree RSS exceeded the 60 GiB safety budget."
                if smi:
                    # B603: fixed read-only NVIDIA query; review if flags become configurable.
                    gpu = subprocess.run(
                        [smi, "--query-gpu=memory.used", "--format=csv,noheader,nounits", "--id=0"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )  # nosec B603
                    if gpu.returncode == 0:
                        peak_vram = max(peak_vram or 0, int(gpu.stdout.strip()) * 1024**2)
                    else:
                        monitor_errors += 1
            except (psutil.Error, ValueError, OSError, subprocess.TimeoutExpired):
                monitor_errors += 1
            if time.monotonic() - start > timeout:
                termination = f"Stage exceeded the {timeout}-second timeout."
            if termination:
                stop_tree(process)
                break
            time.sleep(0.5)
    except BaseException:
        stop_tree(process)
        raise
    finally:
        reader.join(timeout=15)
        if process.stdout:
            process.stdout.close()
    return {
        "name": stage.name,
        "args": stage.args,
        "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "peak_process_tree_rss_bytes": peak_rss,
        "peak_host_used_bytes": peak_host,
        "peak_device_vram_bytes": peak_vram,
        "monitor_errors": monitor_errors,
        "disk_free_delta_bytes": free_before - shutil.disk_usage(cache).free,
        "termination": termination,
        "log": log_path.name,
    }


def dataset_config(run: Path, cache: Path, family: str) -> None:
    def quote(path: Path) -> str:
        return json.dumps(path.as_posix(), ensure_ascii=False)

    frames = 17 if family == "wan" else 124
    (run / "dataset.toml").write_text(
        f'[general]\nresolution = [256, 256]\nbatch_size = 1\nenable_bucket = false\n\n[[datasets]]\nvideo_directory = {quote(run / "fixture")}\ncache_directory = {quote(cache / "dataset")}\ncaption_extension = ".txt"\ntarget_frames = [{frames}]\nframe_extraction = "head"\n',
        encoding="utf-8",
    )


def execute(family: str, settings: Settings, plan_only: bool, timeout: int) -> tuple[int, Path]:
    validate_paths(settings)
    if not settings.output_root or not settings.cache_root:
        raise ValueError("Save dedicated output and cache roots before running a reference.")
    run_id = family + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = within_root(Path(settings.output_root) / run_id, [Path(settings.output_root)])
    cache = within_root(Path(settings.cache_root) / run_id, [Path(settings.cache_root)])
    run.mkdir(parents=True)
    cache.mkdir(parents=True)
    evidence: dict[str, Any] = {
        "version": 1,
        "family": family,
        "status": "blocked",
        "support_gate": "unresolved",
        "started_utc": datetime.now(UTC).isoformat(),
        "expected_upstream": UPSTREAM,
        "host_ram_bytes": psutil.virtual_memory().total,
        "stages": [],
        "blockers": [],
        "measurement_limits": "RSS/process tree and whole-device VRAM sampled about every 0.5–5.5 seconds; may miss brief peaks. RSS sums can double-count shared pages. Device VRAM includes other GPU applications. Disk deltas include unrelated activity; the sampled 60 GiB process-tree guard is not a hard memory cap.",
    }
    evidence_path = run / "evidence.json"
    atomic_json(evidence_path, evidence)
    try:
        evidence["blockers"].extend(validate_wan(settings) if family == "wan" else validate_h3(settings))
        try:
            evidence["environment"] = training_environment(settings, reference_environment(cache))
        except ValueError as error:
            evidence["blockers"].append(str(error))
        try:
            revision = upstream_revision(Path(settings.checkout))
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            evidence["blockers"].append(redact(str(error)))
            return 2, evidence_path
        evidence["upstream_revision"] = revision
        if evidence["blockers"]:
            return 2, evidence_path
        if revision != UPSTREAM:
            raise ValueError(
                "Upstream revision differs from the pinned recipe; do not silently upgrade. Validate a new recipe deliberately."
            )
        if not evidence["environment"].get("cuda_available"):
            raise ValueError("Selected training Python cannot use CUDA.")
        if "3090" not in evidence["environment"].get("gpu", {}).get("name", ""):
            raise ValueError("Target qualification requires the RTX 3090.")
        evidence["models"] = {
            name: {
                "path": value,
                "bytes": Path(value).stat().st_size,
                "header_sha256": hashlib.sha256(
                    json.dumps(safetensors_header(Path(value)), sort_keys=True).encode()
                ).hexdigest()
                if value.endswith(".safetensors")
                else None,
            }
            for name, value in settings.models.items()
            if name.startswith("wan_" if family == "wan" else "h3_") and value
        }
        dataset_config(run, cache, family)
        stages = wan_stages(settings, run, cache) if family == "wan" else h3_stages(settings, run, cache)
        evidence["plan"] = [{"name": stage.name, "args": stage.args} for stage in stages]
        evidence["cache_root"] = str(cache)
        if plan_only:
            evidence["status"] = "plan_only"
            return 0, evidence_path
        evidence["status"] = "running"
        for stage in stages:
            measurement = stage_run(stage, settings, run, cache, timeout)
            evidence["stages"].append(measurement)
            atomic_json(evidence_path, evidence)
            if measurement["exit_code"] != 0 or measurement["termination"]:
                evidence["status"] = "failed"
                evidence["blockers"].append(f"{stage.name} failed; see the redacted stage log and measurements.")
                return 2, evidence_path
            if family == "h3" and stage.name == "latent_cache":
                summary = (run / "latent_cache.log").read_text(encoding="utf-8")
                fraction = re.search(r"supervised_audio_fraction=(\d+\.\d+)", summary)
                if not fraction or float(fraction.group(1)) != 1.0:
                    raise ValueError(
                        "H3 fixture must have 100% real-audio supervision; latent cache summary did not confirm this."
                    )
                evidence["audio_supervision_fraction"] = float(fraction.group(1))
        checkpoint = run / "checkpoints" / f"{family}-reference.safetensors"
        samples = sorted((run / "samples").glob("*.mp4"))
        if not checkpoint.is_file() or not samples:
            raise ValueError("The scenario exited without the expected final checkpoint and MP4 sample.")
        inspect_stage = Stage(
            "inspect_sample",
            [*python_args(settings), str(Path(__file__).with_name("fixture.py")), "inspect", str(samples[0])],
        )
        result = stage_run(inspect_stage, settings, run, cache, timeout)
        evidence["stages"].append(result)
        lines = (run / "inspect_sample.log").read_text(encoding="utf-8").splitlines()
        media: dict[str, Any] = next(
            (json.loads(line.removeprefix("MUSUBI_MEDIA=")) for line in lines if line.startswith("MUSUBI_MEDIA=")), {}
        )
        if result["exit_code"] or not media.get("video_frames"):
            raise ValueError("Generated video did not decode; this run does not establish playable output.")
        if family == "h3" and not media.get("audio_samples"):
            raise ValueError("H3 generated sample has no decodable audio; T2VA support remains unresolved.")
        evidence.update(status="scenario_passed", checkpoint=str(checkpoint), sample=str(samples[0]), media=media)
        # The user corrected the target to the existing nominal 96 GiB machine.
        # Allow up to one GiB reserved by firmware/Windows when matching that class.
        if 95 * 1024**3 <= psutil.virtual_memory().total <= 97 * 1024**3:
            evidence["support_gate"] = "passed"
        else:
            evidence["blockers"].append("Qualification on the specified nominal 96 GB host remains required.")
        evidence["limitations"] = (
            "A short execution scenario does not establish learning quality or production-scale capacity. Human playback/quality review is recommended."
        )
        return 0, evidence_path
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        evidence["status"] = "blocked"
        evidence["blockers"].append(redact(str(error)))
        return 2, evidence_path
    except KeyboardInterrupt:
        evidence["status"] = "interrupted"
        evidence["blockers"].append("Interrupted by the operator; support remains unresolved.")
        return 130, evidence_path
    finally:
        evidence["finished_utc"] = datetime.now(UTC).isoformat()
        atomic_json(evidence_path, evidence)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=["wan", "h3"])
    parser.add_argument("--settings", type=Path, default=Path(".state/settings.json"))
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--stage-timeout", type=int, default=3600)
    args = parser.parse_args()
    try:
        settings = Settings.model_validate_json(args.settings.read_text(encoding="utf-8"))
        if settings.version != 1:
            raise ValueError("Unsupported settings version.")
        code, evidence = execute(args.family, settings, args.plan_only, args.stage_timeout)
    except (ValueError, OSError) as error:
        print(redact(str(error)))
        raise SystemExit(2) from error
    print(f"Evidence: {evidence}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()

"""Whole-Run forecasts from frozen schedules and measured wall time, with explicit gaps."""

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from musubi_gui.wan_pair_runtime import stage_log


def timing_file(directory: Path, stage: dict[str, Any]) -> Path:
    return directory / ("timing-" + Path(stage_log(stage)).stem + ".json")


def freeze_training_timing(stage: dict[str, Any], family: str, directory: Path) -> dict[str, Any]:
    if stage["name"] != "train":
        return stage
    args = list(stage["args"])
    path = str(timing_file(directory, stage))
    if "--timing-file" in args:
        args[args.index("--timing-file") + 1] = path
    else:
        index = next(
            (
                i
                for i, arg in enumerate(args)
                if Path(arg).name
                in {
                    "wan_train_network.py",
                    "minimax_h3_train_network.py",
                }
            ),
            None,
        )
        if index is None:
            return stage  # Trusted custom adapters and old commands remain supported.
        args[index : index + 1] = [
            str(Path(__file__).with_name("timed_trainer.py")),
            "--timing-file",
            path,
            "--family",
            family,
            "--upstream",
            args[index],
        ]
    return {**stage, "args": args}


def seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (ValueError, TypeError):
        return None


def number(data: dict[str, Any], key: str) -> float:
    value = data.get(key, 0)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else 0.0


def read_timing(directory: Path, stage: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(timing_file(directory, stage).read_text(encoding="utf-8"))
        if data.get("version") == 1 and all(isinstance(data.get(key), dict) for key in ("training", "previews")):
            return dict(data)
    except (OSError, ValueError, AttributeError):
        pass
    return {"training": {}, "previews": {}}


def argument_number(stage: dict[str, Any], flag: str) -> int:
    args = stage["args"]
    try:
        return max(0, int(args[args.index(flag) + 1]))
    except (ValueError, IndexError):
        return 0


def sampling_group(stage: dict[str, Any]) -> tuple[str, ...]:
    if stage["name"] == "train":
        return ("native",)
    args = stage["args"]
    values = ["standalone"]
    for flag, count in [
        ("--video_size", 2),
        ("--video_length", 1),
        ("--infer_steps", 1),
        ("--blocks_to_swap", 1),
        ("--text_encoder_blocks_to_swap", 1),
        ("--fp8", 0),
        ("--fp8_scaled", 0),
        ("--sdpa", 0),
    ]:
        if flag in args:
            index = args.index(flag)
            values.extend([flag, *args[index + 1 : index + count + 1]])
    return tuple(values)


def completion_estimate(run: dict[str, Any], progress: dict[str, Any] | None, now: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "as_of": now,
        "remaining_seconds": None,
        "finish_at": None,
        "training_remaining_seconds": None,
        "preview_remaining_seconds": None,
        "sampling_remaining_seconds": None,
        "overhead_remaining_seconds": 0,
        "outstanding_previews": 0,
        "outstanding_final_samples": 0,
        "completed_previews": 0,
        "unknown_components": [],
    }
    if run["status"] not in {"training", "generating"}:
        if run["status"] == "completed":
            result.update(
                remaining_seconds=0,
                training_remaining_seconds=0,
                preview_remaining_seconds=0,
                sampling_remaining_seconds=0,
            )
        return result
    directory = Path(run["directory"])
    stages = run["stages"]
    observations = [read_timing(directory, stage) for stage in stages]
    gaps: set[str] = set()
    rates: dict[str, list[tuple[float, float]]] = {}
    first_steps: dict[str, list[float]] = {}
    final_saves: dict[str, list[float]] = {}
    preview_times: dict[tuple[str, ...], list[tuple[float, int]]] = {}

    def expert(stage: dict[str, Any]) -> str:
        return str(stage.get("training", {}).get("expert", "native"))

    # Native previews and standalone samples have different residency/loading costs.
    for stage, observed in zip(stages, observations, strict=True):
        training, previews = observed["training"], observed["previews"]
        if stage["name"] == "train":
            key = expert(stage)
            intervals, seconds = number(training, "intervals"), number(training, "seconds")
            if intervals and seconds:
                rates.setdefault(key, []).append((seconds, intervals))
            first = seconds_between(stage.get("started"), training.get("first_step_at"))
            if first is not None:
                first_steps.setdefault(key, []).append(first)
            if stage["status"] == "completed":
                tails = [
                    value
                    for end in (previews.get("last_finished"), training.get("last_step_at"))
                    if (value := seconds_between(end, stage.get("finished"))) is not None
                ]
                if tails:
                    final_saves.setdefault(key, []).append(min(tails))
            count = int(number(previews, "completed"))
            duration = number(previews, "seconds")
            if count and duration:
                preview_times.setdefault(sampling_group(stage), []).append((duration, count))
        elif stage["name"] == "generate" and stage["status"] == "completed":
            sample_duration = seconds_between(stage.get("started"), stage.get("finished"))
            if sample_duration is not None and sample_duration > 0:
                preview_times.setdefault(sampling_group(stage), []).append((sample_duration, 1))

    def mean(values: list[float]) -> float:
        return sum(values) / len(values)

    startup_rates = set(first_steps) - set(rates)
    for key in startup_rates:
        # One-update phases never expose two optimizer observations. Their full
        # first-update interval includes loading, so retain that conservative cost.
        values = first_steps[key]
        if sum(values) > 0:
            rates[key] = [(sum(values), float(len(values)))]

    training_seconds = 0.0
    training_known = True
    preview_seconds = 0.0
    sampling_seconds = 0.0
    previews_known = True
    sampling_known = True
    overhead = 0.0
    live_training = next(
        (i for i in reversed(range(len(stages))) if stages[i]["name"] == "train" and stages[i]["status"] != "pending"),
        None,
    )
    for index, (stage, observed) in enumerate(zip(stages, observations, strict=True)):
        name = stage["name"]
        if name not in {"train", "generate"}:
            continue
        done = stage["status"] in {"completed", "reused"}
        if name == "train":
            total = argument_number(stage, "--max_train_steps")
            training, previews = observed["training"], observed["previews"]
            step = total if done else int(number(training, "step"))
            if not step and progress and index == live_training:
                step = int(progress.get("phase_step", progress["step"]))
            remaining = max(0, total - step)
            key = expert(stage)
            measured = rates.get(key, [])
            if not measured and rates:
                measured = [item for values in rates.values() for item in values]
                if remaining:
                    gaps.add("unmeasured expert speed (using observed expert)")
            if not done:
                if remaining and measured:
                    if key in startup_rates:
                        gaps.add("training rate includes first-update loading (no steady interval yet)")
                    rate = sum(s for s, _ in measured) / sum(n for _, n in measured)
                    training_seconds += remaining * rate
                    if stage["status"] == "pending" and first_steps.get(key):
                        # The first update already belongs to the measured full startup interval.
                        overhead += max(0, mean(first_steps[key]) - rate)
                    elif stage["status"] == "pending":
                        gaps.add("future model loading and expert switching")
                elif remaining:
                    # Legacy bars may already contain previews. Do not add previews to that ETA.
                    training_known = False
                    gaps.add("training steps without preview time")
                if final_saves.get(key):
                    overhead += mean(final_saves[key])
                else:
                    gaps.add("checkpoint/state saving and stage transitions")
            interval = argument_number(stage, "--sample_every_n_steps")
            scheduled = total // interval if interval else 0
            completed = scheduled if done else min(scheduled, int(number(previews, "completed")))
            result["completed_previews"] += completed
            outstanding = 0 if done else max(0, scheduled - completed)
            if number(previews, "failed") and not done:
                previews_known = False
                gaps.add("failed or incomplete previews")
            active = previews.get("active") if isinstance(previews.get("active"), dict) else None
        else:
            scheduled, completed = 1, int(done)
            outstanding = int(not done)
            active = {"started": stage.get("started")} if stage["status"] == "running" else None
            if stage.get("preview_step") is not None:
                result["completed_previews"] += completed
            else:
                result["outstanding_final_samples"] += outstanding
        is_preview = name == "train" or stage.get("preview_step") is not None
        if is_preview:
            result["outstanding_previews"] += outstanding
        if not outstanding:
            continue
        measured_previews = preview_times.get(sampling_group(stage), [])
        if not measured_previews:
            if is_preview:
                previews_known = False
            else:
                sampling_known = False
            gaps.add("periodic previews" if is_preview else "final sampling")
            continue
        average = sum(s for s, _ in measured_previews) / sum(n for _, n in measured_previews)
        seconds = outstanding * average
        if active:
            elapsed = seconds_between(active.get("started"), now)
            if elapsed is None:
                gaps.add("active preview start time")
            else:
                seconds -= min(average, elapsed)
                if elapsed > average:
                    gaps.add("active sampling exceeds its measured budget")
        if is_preview:
            preview_seconds += seconds
        else:
            sampling_seconds += seconds
    result.update(
        training_remaining_seconds=math.ceil(training_seconds) if training_known else None,
        preview_remaining_seconds=math.ceil(preview_seconds) if previews_known else None,
        sampling_remaining_seconds=math.ceil(sampling_seconds) if sampling_known else None,
        overhead_remaining_seconds=math.ceil(overhead),
        unknown_components=sorted(gaps),
    )
    if training_known and previews_known and sampling_known:
        remaining_seconds = math.ceil(training_seconds + preview_seconds + sampling_seconds + overhead)
        result.update(
            remaining_seconds=remaining_seconds,
            finish_at=(datetime.fromisoformat(now).astimezone(UTC) + timedelta(seconds=remaining_seconds)).isoformat(),
        )
    return result

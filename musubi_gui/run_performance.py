"""Best-effort external H3 diagnostics; never synchronizes CUDA or imports Torch."""

import csv
import json
import math
import os
import shutil
import subprocess  # nosec B404
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.processes import matching_process, process_identity
from musubi_gui.redaction import redact
from musubi_gui.run_progress import parse_progress

GPU_FIELDS = {
    "index": "index",
    "name": "name",
    "uuid": "uuid",
    "memory.used": "memory_used_mib",
    "memory.total": "memory_total_mib",
    "utilization.gpu": "utilization_percent",
    "utilization.memory": "memory_utilization_percent",
    "power.draw": "power_watts",
    "power.limit": "power_limit_watts",
    "temperature.gpu": "temperature_celsius",
    "clocks.current.graphics": "graphics_clock_mhz",
    "clocks.current.memory": "memory_clock_mhz",
    "pstate": "pstate",
}


def gpu_samples(executable: str | None) -> dict[str, Any]:
    if not executable:
        return {"gpus": None, "gpu_error": "nvidia-smi unavailable (or disabled for fixture)"}
    # B603: fixed read-only query, discovered executable, no shell or user-supplied flags.
    result = subprocess.run(  # nosec B603
        [executable, "--query-gpu=" + ",".join(GPU_FIELDS), "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        return {"gpus": None, "gpu_error": f"nvidia-smi exited {result.returncode}"}
    devices = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != len(GPU_FIELDS):
            raise ValueError("Unexpected nvidia-smi column count")
        device: dict[str, Any] = {}
        for (field, key), raw in zip(GPU_FIELDS.items(), row, strict=True):
            value = raw.strip()
            if field in {"index", "name", "uuid", "pstate"}:
                device[key] = value if value and not value.startswith("[") else None
            else:
                try:
                    number = float(value)
                    device[key] = number if math.isfinite(number) else None
                except ValueError:
                    device[key] = None
        devices.append(device)
    return {"gpus": devices, "gpu_error": None}


class RunPerformance:
    """One bounded, cancellable collector per stage. Failures only disable diagnostics."""

    def __init__(self, run: dict[str, Any], stage: dict[str, Any], pid: int, log: Path, *, fixture: bool):
        self.path = Path(run["directory"]) / "performance.jsonl"
        self.log = log
        self.stage = stage["name"]
        self.start = time.monotonic()
        self.stop = threading.Event()
        try:
            self.identity: dict[str, Any] | None = process_identity(pid)
        except (psutil.Error, OSError):
            self.identity = None
        self.smi = None if fixture else shutil.which("nvidia-smi")
        self.previous: dict[tuple[int, float], tuple[float, int, int]] = {}
        self.previous_at: float | None = None
        self.previous_step: tuple[int, float] | None = None
        self.thread = threading.Thread(target=self._collect, name="musubi-performance", daemon=True)
        self.enabled = self._write(
            {
                "event": "stage_start",
                "version": 1,
                "run_id": run["id"],
                "fixture": fixture,
                "interval_seconds": 5,
                "config": run["job"].get("config", {}),
                "args": stage["args"],
                "provenance": run.get("provenance", {}),
                "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            }
        )
        if self.enabled:
            self.thread.start()

    def _write(self, data: dict[str, Any]) -> bool:
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    redact(
                        json.dumps(
                            {
                                "timestamp": datetime.now(UTC).isoformat(),
                                "stage": self.stage,
                                "stage_elapsed_seconds": round(time.monotonic() - self.start, 3),
                                **data,
                            },
                            allow_nan=False,
                        )
                    )
                    + "\n"
                )
            return True
        except (OSError, ValueError):
            try:
                with self.log.open("a", encoding="utf-8") as stream:
                    stream.write("\nPerformance diagnostics unavailable; training is unaffected.\n")
            except OSError:
                pass
            return False

    def _host(self, now: float) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        parent = matching_process(self.identity) if self.identity else None
        processes = [parent, *parent.children(recursive=True)] if parent else []
        current = {}
        rss = 0
        incomplete = False
        for process in processes:
            try:
                with process.oneshot():
                    key = (process.pid, process.create_time())
                    rss += process.memory_info().rss
                    cpu = process.cpu_times()
                    io = process.io_counters()
                    current[key] = (cpu.user + cpu.system, io.read_bytes, io.write_bytes)
            except (psutil.Error, OSError):
                incomplete = True
        deltas = [
            tuple(max(0, value - old) for value, old in zip(counters, self.previous[key], strict=True))
            for key, counters in current.items()
            if key in self.previous
        ]
        elapsed = now - self.previous_at if self.previous_at is not None else 0
        comparable = bool(deltas) and elapsed > 0
        self.previous, self.previous_at = current, now
        return {
            "host_available_bytes": memory.available,
            "host_memory_percent": memory.percent,
            "process_tree_rss_bytes": rss if current else None,
            "process_tree_cpu_percent": sum(d[0] for d in deltas) / elapsed * 100 if comparable else None,
            "process_tree_read_bytes_per_second": sum(d[1] for d in deltas) / elapsed if comparable else None,
            "process_tree_write_bytes_per_second": sum(d[2] for d in deltas) / elapsed if comparable else None,
            "process_count": len(current),
            "process_sample_incomplete": incomplete or not current,
        }

    def _progress(self, now: float) -> dict[str, Any] | None:
        with self.log.open("rb") as stream:
            stream.seek(max(0, self.log.stat().st_size - 64_000))
            text = stream.read().decode("utf-8", errors="replace")
        progress = parse_progress(text)
        if progress is None:
            return None
        progress["observed_seconds_per_step"] = None
        if self.previous_step is not None:
            step, observed_at = self.previous_step
            if progress["step"] > step:
                progress["observed_seconds_per_step"] = (now - observed_at) / (progress["step"] - step)
        if self.previous_step is None or progress["step"] != self.previous_step[0]:
            self.previous_step = (progress["step"], now)
        return progress

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        data: dict[str, Any] = {"event": "sample", "errors": []}
        collectors: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("host", lambda: self._host(now)),
            ("gpu", lambda: gpu_samples(self.smi)),
            ("progress", lambda: {"progress": self._progress(now)}),
        ]
        for name, collect in collectors:
            try:
                data.update(collect())
            except (OSError, ValueError, RuntimeError, psutil.Error, subprocess.SubprocessError) as error:
                data["errors"].append(f"{name}: {type(error).__name__}")
        data["collection_seconds"] = round(time.monotonic() - now, 3)
        return data

    def _collect(self) -> None:
        while not self.stop.is_set():
            if not self._write(self.sample()):
                return
            self.stop.wait(5)

    def close(self, exit_code: int | None = None) -> None:
        self.stop.set()
        if self.enabled:
            # The only blocking subprocess has a 10-second hard timeout and is reaped by run().
            self.thread.join(timeout=15)
            try:
                self._write({"event": "progress_at_stop", "progress": self._progress(time.monotonic())})
            except (OSError, ValueError):
                pass
            self._write({"event": "monitor_stop", "exit_code": exit_code})


def start_performance(
    run: dict[str, Any], stage: dict[str, Any], pid: int, log: Path, *, fixture: bool
) -> RunPerformance | None:
    if run["job"]["family"] != "h3":
        return None
    try:
        return RunPerformance(run, stage, pid, log, fixture=fixture)
    except (OSError, ValueError, psutil.Error):
        # A stage can finish before its launcher identity is observed.
        return None

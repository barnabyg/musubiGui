"""Validate and seal locally produced optimizer state without confusing it with LoRA weights."""

import hashlib
import json
import subprocess  # nosec B404
import time
from pathlib import Path
from threading import Event
from typing import Any

import psutil

from musubi_gui.processes import matching_process, observed_tree, terminate_owned
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import within_root
from musubi_gui.snapshots import digest_file

REQUIRED = ("model.safetensors", "optimizer.bin", "scheduler.bin", "random_states_0.pkl")


def inspect_state(
    run: dict[str, Any], directory: Path, previous: dict[str, Any] | None, *, cancelled: Event | None = None
) -> dict[str, Any]:
    state: dict[str, Any] = {
        **(previous or {}),
        "id": hashlib.sha256(directory.name.encode()).hexdigest()[:24],
        "name": directory.name,
        "path": str(directory),
        "valid": False,
        "error": "",
    }
    try:
        within_root(directory, [Path(run["directory"]) / "checkpoints"])
        files = {}
        for name in REQUIRED:
            path = within_root(directory / name, [directory])
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Incomplete saved state: model, optimizer, scheduler and RNG files are all required.")
            files[name] = digest_file(path, cancelled=cancelled)
        if previous and any(previous.get("files", {}).get(name) != value for name, value in files.items()):
            raise ValueError("Saved state files changed after they were recorded. Restore the original complete state.")
        safetensors_header(directory / "model.safetensors")
        if run["fixture"]:
            data = json.loads((directory / "trainer_state.json").read_text(encoding="utf-8"))
            if data.get("version") != 1 or not data.get("synthetic") or not isinstance(data.get("optimizer_step"), int):
                raise ValueError("Incomplete synthetic state; no real optimizer restoration is claimed.")
            probe = {"valid": True, "optimizer_step": data["optimizer_step"], "synthetic": True}
            files["trainer_state.json"] = digest_file(directory / "trainer_state.json", cancelled=cancelled)
            if previous and previous.get("files") != files:
                raise ValueError("Saved state changed after it was recorded.")
        else:
            # B603: fixed read-only probe through the explicitly selected training Python; no shell.
            args = [
                run["settings"]["python"],
                "-I",
                "-B",
                "-X",
                "utf8",
                str(Path(__file__).with_name("state_probe.py")),
                str(directory),
            ]
            code, output = state_probe(args, cancelled)
            lines = [line for line in output.splitlines() if line.startswith("MUSUBI_STATE=")]
            if code or not lines:
                raise ValueError("The selected training environment could not validate this saved state.")
            probe = json.loads(lines[-1].removeprefix("MUSUBI_STATE="))
            if not probe.get("valid"):
                raise ValueError(probe.get("error", "Invalid saved optimizer state."))
        state.update(probe, files=files, origin=run["id"], upstream=run.get("provenance", {}).get("upstream_revision"))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as error:
        state["error"] = str(error)
    return state


def state_probe(args: list[str], cancelled: Event | None) -> tuple[int, str]:
    """Bound the fixed state probe and reap it promptly when background inspection is cancelled."""
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("State inspection cancelled during service shutdown.")
    # B603: caller builds the fixed probe argument array using the selected training executable; no shell.
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )  # nosec B603
    deadline = time.monotonic() + 90
    identities: dict[tuple[int, float], dict[str, Any]] = {}
    try:
        while True:
            for identity in observed_tree(process.pid):
                identities[(identity["pid"], identity["created"])] = identity
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError("State inspection cancelled during service shutdown.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, 90)
            try:
                output, _ = process.communicate(timeout=min(0.2, remaining))
                return process.returncode, output
            except subprocess.TimeoutExpired:
                continue
    finally:
        cleanup_deadline = time.monotonic() + 30
        for identity in observed_tree(process.pid):
            identities[(identity["pid"], identity["created"])] = identity
        # Windows venv launchers start the actual interpreter as a child; killing the launcher alone leaks it.
        owned = [child for identity in identities.values() if (child := matching_process(identity)) is not None]
        terminate_owned(list(identities.values()), force=True)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=30)
        _, alive = psutil.wait_procs(owned, timeout=max(0, cleanup_deadline - time.monotonic()))
        if alive:
            raise ValueError("State probe descendants did not exit after termination.")


def discover_states(run: dict[str, Any]) -> list[dict[str, Any]]:
    previous = {entry["id"]: entry for entry in run.get("saved_states", [])}
    states = []
    for directory in sorted((Path(run["directory"]) / "checkpoints").glob("*-state")):
        if directory.is_dir():
            state_id = hashlib.sha256(directory.name.encode()).hexdigest()[:24]
            states.append(inspect_state(run, directory, previous.get(state_id)))
    for state_id, state in previous.items():
        if not any(entry["id"] == state_id for entry in states):
            states.append(
                {
                    **state,
                    "valid": False,
                    "error": "Saved state directory is missing or moved. No source files were deleted.",
                }
            )
    return states

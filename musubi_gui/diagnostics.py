"""Actionable checks without installing or upgrading training dependencies."""

import json
import os
import shutil
import subprocess  # nosec B404
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from musubi_gui.settings import Settings, atomic_json, validate_paths, within_root

REPORT_VERSION = 1


def record_report(root: Path, settings: Settings, report: dict[str, Any]) -> dict[str, Any]:
    """Keep the latest result beside settings.json, which stays as version 1 wrote it.

    A write failure loses only the memory of this result, so the checks still reach the screen.
    """
    saved = {
        "version": REPORT_VERSION,
        "ran_at": datetime.now(UTC).isoformat(),
        "settings": settings.model_dump(),
        **report,
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        atomic_json(root / "diagnostics.json", saved)
    except OSError:
        pass
    return _view(saved, settings)


def latest_report(root: Path, settings: Settings) -> dict[str, Any] | None:
    """The kept result, or None when there is none this version can read: it can always be run again."""
    try:
        saved = json.loads((root / "diagnostics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("version") != REPORT_VERSION:
        return None
    return _view(saved, settings)


def _view(saved: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        "ran_at": saved.get("ran_at"),
        "checks": saved.get("checks", []),
        "environment": saved.get("environment", {}),
        "current": saved.get("settings") == settings.model_dump(),
    }


def training_environment(
    settings: Settings, environment: dict[str, str] | None = None, *, versions_only: bool = False
) -> dict[str, Any]:
    python = Path(settings.python)
    if not python.is_file():
        raise ValueError("Select the existing training environment's python.exe.")
    env = os.environ.copy() if environment is None else environment.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    # B603: selected local Python is an explicit trust boundary; review if selectable remotely.
    try:
        result = subprocess.run(  # nosec B603
            [
                str(python),
                "-I",
                "-B",
                "-X",
                "utf8",
                str(Path(__file__).with_name("probe.py")),
                *(["--versions-only"] if versions_only else []),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Selected Python could not complete the read-only probe within 90 seconds.") from error
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("MUSUBI_PROBE=") and result.returncode == 0:
            return dict(json.loads(line.removeprefix("MUSUBI_PROBE=")))
    raise ValueError(
        "Selected Python did not return a valid probe. Check that it starts and can import its dependencies."
    )


def upstream_revision(checkout: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise ValueError("Install Git for Windows to record the upstream revision.")
    # B603: fixed read-only Git operation, no shell; review if arguments become user commands.
    result = subprocess.run(  # nosec B603
        [git, "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    revision = result.stdout.strip()
    if result.returncode or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Selected checkout has no readable Git revision. Use an existing Git clone of musubi-tuner.")
    return revision


def diagnostics(settings: Settings) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    environment: dict[str, Any] = {}

    def check(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    try:
        validate_paths(settings)
    except ValueError as error:
        check("Paths", "error", str(error))
        return {"checks": checks, "environment": environment}
    checkout = Path(settings.checkout)
    if not settings.checkout or not all(
        (checkout / p).is_file() for p in ["wan_train_network.py", "minimax_h3_train_network.py"]
    ):
        check("Installation", "error", "Select a musubi-tuner checkout containing both training entry points.")
    else:
        check("Installation", "ok", "Training entry points found.")
        try:
            environment["upstream_revision"] = upstream_revision(checkout)
            check("Revision", "ok", environment["upstream_revision"])
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            check(
                "Revision",
                "error",
                str(error) if isinstance(error, ValueError) else "Git revision check failed; check Git access.",
            )
    try:
        environment.update(training_environment(settings))
        packages = environment.get("packages", {})
        if environment.get("torch_error") == "missing":
            check(
                "PyTorch", "error", "Install PyTorch in the selected training environment; the GUI will not change it."
            )
        elif environment.get("torch_error"):
            check("PyTorch", "error", "PyTorch cannot initialize; check the selected environment and NVIDIA driver.")
        missing = [name for name, version in packages.items() if version is None and name != "musubi-tuner"]
        if missing:
            check(
                "Dependencies",
                "error",
                "Missing training dependencies: " + ", ".join(missing) + ". Follow this checkout's installation guide.",
            )
        else:
            check("Dependencies", "ok", "Required training packages found.")
        if environment.get("cuda_available"):
            gpu = environment["gpu"]
            check(
                "GPU",
                "ok" if gpu["vram_bytes"] >= 23 * 1024**3 else "warning",
                f"{gpu['name']}: {gpu['vram_bytes'] / 1024**3:.1f} GiB VRAM. Reference runs are required to prove model support.",
            )
        else:
            check(
                "GPU",
                "error",
                "CUDA is unavailable in selected Python; check CUDA-enabled PyTorch and the NVIDIA driver.",
            )
    except ValueError as error:
        check("Python", "error", str(error))
    ram = psutil.virtual_memory().total
    environment["host_ram_bytes"] = ram
    check(
        "Host RAM",
        "ok" if ram >= 95 * 1024**3 else "warning",
        f"{ram / 1024**3:.1f} GiB installed; per-stage measurements must establish the nominal 96 GB recipe budget.",
    )
    for name, location in [("Output", settings.output_root), ("Cache", settings.cache_root)]:
        if not location:
            check(name, "error", f"Choose a dedicated absolute {name.lower()} root.")
            continue
        try:
            root = within_root(Path(location), [Path(location)])
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=root):
                pass
            free = shutil.disk_usage(root).free
            environment[f"{name.lower()}_free_bytes"] = free
            check(
                name,
                "ok" if free >= 400_000_000_000 else "warning",
                f"Writable; {free / 10**9:.1f} GB free. Budget weights, caches, temporary files and retained outputs before training.",
            )
        except OSError:
            check(
                name, "error", f"Cannot write to the configured {name.lower()} root; check permissions and free disk."
            )
    if not settings.model_roots:
        check("Models", "warning", "Configure local model roots and component paths before reference runs.")
    for name, roots, note in [
        ("Model roots", settings.model_roots, ""),
        ("Dataset roots", settings.dataset_roots, " Creating a managed dataset creates a missing root."),
    ]:
        absent = [root for root in roots if not Path(root).is_dir()]
        if absent:
            check(
                name,
                "warning",
                "Not an existing folder: "
                + "; ".join(absent)
                + ". Check for mistyped or joined paths; put each root on its own line."
                + note,
            )
    for name, value in settings.models.items():
        if value and not Path(value).is_file():
            check(name, "error", "Model component is missing; select an existing file inside a configured model root.")
    return {"checks": checks, "environment": environment}

"""Canonical non-mutating source checks. Builds write ignored outputs only."""

import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path


def run(*args: str) -> None:
    print("\n> " + " ".join(args), flush=True)
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["PIP_CACHE_DIR"] = str(Path(".cache/pip").resolve())
    env["UV_CACHE_DIR"] = str(Path(".cache/uv").resolve())
    # B603: callers below supply fixed verification commands; review if external arguments are added.
    subprocess.run(args, check=True, env=env)  # nosec B603


def main() -> None:
    python = sys.executable
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Node.js/npm is required.")
    run(python, "-m", "ruff", "format", "--check", "musubi_gui", "tools", "tests/python")
    run(npm, "run", "format:check")
    run(python, "-m", "ruff", "check", "musubi_gui", "tools", "tests/python")
    run(npm, "run", "lint")
    run(python, "-m", "mypy")
    run(npm, "run", "typecheck")
    run(python, "-m", "bandit", "-r", "musubi_gui", "tools", "-c", "pyproject.toml", "-q")
    # Browser tests exercise the bundled build; packaging is validated again below.
    run(npm, "run", "build")
    run(python, "-m", "pytest", "-q")
    run(npm, "run", "test:browser")
    run("uv", "lock", "--check")
    run(npm, "ls", "--all")
    run(python, "-m", "pip_audit", "--disable-pip", "--progress-spinner", "off")
    run(npm, "audit", "--audit-level=low")
    scanner = shutil.which("detect-secrets") or str(Path(sys.executable).parent / "detect-secrets.exe")
    # B603: fixed scanner and source paths; review if external arguments are added.
    result = subprocess.run(  # nosec B603
        [scanner, "scan", "--all-files", "musubi_gui", "frontend", "tools", "tests"],
        capture_output=True,
        text=True,
        check=True,
    )
    findings = json.loads(result.stdout)["results"]
    if findings:
        raise SystemExit("Secret scanner found possible secrets in: " + ", ".join(findings))
    run(python, "-m", "build", "--no-isolation")
    print("\nVerification passed.")


if __name__ == "__main__":
    main()

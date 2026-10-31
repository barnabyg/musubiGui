"""Canonical non-mutating source checks. Builds write ignored outputs only."""

import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from zipfile import ZipFile


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
    run(python, "-m", "pytest", "-q")
    # Builds the UI first through pretest:browser; packaging below ships that same bundle.
    run(npm, "run", "test:browser")
    run("uv", "lock", "--check")
    run(npm, "ls", "--all")
    audit_requirements = str(Path(".cache/audit-requirements.txt").resolve())
    run(
        "uv",
        "export",
        "--locked",
        "--all-groups",
        "--no-emit-project",
        "--quiet",
        "--output-file",
        audit_requirements,
    )
    run(
        python,
        "-m",
        "pip_audit",
        "--disable-pip",
        "--require-hashes",
        "-r",
        audit_requirements,
        "--progress-spinner",
        "off",
    )
    run(npm, "audit", "--audit-level=low")
    scanner = shutil.which("detect-secrets") or str(Path(sys.executable).parent / "detect-secrets.exe")
    git = shutil.which("git")
    if not git:
        raise SystemExit("Git is required to enumerate tracked and new source files for secret checks.")
    # B603: fixed read-only enumeration, includes new files but excludes ignored runtime artifacts.
    tracked = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )  # nosec B603
    sources = [name for name in tracked.stdout.split("\x00") if name and Path(name).is_file()]
    # B603: fixed scanner and source paths; review if external arguments are added.
    result = subprocess.run(  # nosec B603
        [
            scanner,
            "scan",
            "--all-files",
            "--exclude-lines",
            r'^\s*"(?:expected_upstream|upstream_revision|application_revision|header_sha256)": "[a-f0-9]{40}(?:[a-f0-9]{24})?",?$',
            *sources,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    findings = json.loads(result.stdout)["results"]
    if findings:
        raise SystemExit("Secret scanner found possible secrets in: " + ", ".join(findings))
    run(python, "-m", "build", "--no-isolation")
    with ZipFile(next(Path("dist").glob("*.whl"))) as wheel:
        names = wheel.namelist()
        if "musubi_gui/web/index.html" not in names or not all(
            any(name.startswith("musubi_gui/web/assets/") and name.endswith(suffix) for name in names)
            for suffix in [".js", ".css"]
        ):
            raise SystemExit("Wheel is missing the built browser application.")
    print("\nVerification passed.")


if __name__ == "__main__":
    main()

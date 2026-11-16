"""Canonical non-mutating source checks. Builds write ignored outputs only.

With no arguments every check runs. CI runs the parts on separate runners at once:
`static` for everything except the test suites, and `python` or `browser` for one suite,
optionally split with `--shard INDEX/TOTAL`.
"""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zipfile import ZipFile


def run(*args: str) -> None:
    print("\n> " + " ".join(args), flush=True)
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env["PIP_CACHE_DIR"] = str(Path(".cache/pip").resolve())
    env["UV_CACHE_DIR"] = str(Path(".cache/uv").resolve())
    # B603: callers below supply fixed verification commands; review if external arguments are added.
    subprocess.run(args, check=True, env=env)  # nosec B603


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", nargs="?", choices=["static", "python", "browser"], help="run only this part")
    parser.add_argument("--shard", metavar="INDEX/TOTAL", help="run one share of the python or browser suite")
    args = parser.parse_args()
    if args.shard and args.part not in ("python", "browser"):
        parser.error("--shard needs the python or browser part")
    if args.shard:
        index, _, total = args.shard.partition("/")
        if not (index.isdigit() and total.isdigit() and 1 <= int(index) <= int(total)):
            parser.error(f"--shard must be INDEX/TOTAL with INDEX from 1 to TOTAL, not {args.shard!r}")
    return args


def browser_shard_list(shard: str) -> Path:
    """Writes this shard's Playwright test list, dealing the tests out in turn as the pytest shard does.

    Playwright's own --shard takes a contiguous run of tests, which puts the slow Run specs together.
    """
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not npx:
        raise SystemExit("Node.js/npx is required.")
    # B603: fixed read-only listing command.
    listing = subprocess.run(  # nosec B603
        [npx, "playwright", "test", "--list", "--reporter=json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    tests = list(browser_test_titles(json.loads(listing.stdout)["suites"]))
    index, total = (int(part) for part in shard.split("/"))
    path = Path(f".cache/browser-shard-{index}-of-{total}.txt").resolve()
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(f"{test}\n" for test in tests[index - 1 :: total]), encoding="utf-8")
    return path


def browser_test_titles(suites: list[dict[str, Any]], titles: tuple[str, ...] = ()) -> Iterator[str]:
    """Yields `file › describe › test` lines for --test-list, in Playwright's run order."""
    for suite in suites:
        path = (*titles, suite["title"])
        for spec in suite.get("specs", []):
            yield " › ".join((*path, spec["title"]))
        yield from browser_test_titles(suite.get("suites", []), path)


def main() -> None:
    args = parse_args()
    python = sys.executable
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Node.js/npm is required.")
    shard = [f"--shard={args.shard}"] if args.shard else []
    if args.part in (None, "static"):
        code_checks(python, npm)
    if args.part in (None, "python"):
        # CI stops at the first failure; local runs report every failure. Hosted runners get fewer workers.
        # CI also keeps each test's hosted duration for balancing shards.
        durations = str(Path(".cache/durations/python.xml").resolve())
        options = ["--maxfail=1", "-n", "2", f"--junitxml={durations}"] if os.environ.get("CI") else ["-n", "4"]
        # The explicit path loads tests/python/conftest.py before options parse, so --shard exists.
        run(python, "-m", "pytest", "tests/python", "-q", *options, *shard)
    if args.part in (None, "browser"):
        # Builds the UI first through pretest:browser; packaging below ships that same bundle.
        test_list = ["--", f"--test-list={browser_shard_list(args.shard)}"] if args.shard else []
        run(npm, "run", "test:browser", *test_list)
    if args.part == "static":
        # Without the browser suite, nothing else builds the UI that packaging ships.
        run(npm, "run", "build")
    if args.part in (None, "static"):
        dependency_and_package_checks(python, npm)
    print("\nVerification passed.")


def code_checks(python: str, npm: str) -> None:
    run(python, "-m", "ruff", "format", "--check", "musubi_gui", "tools", "tests/python", "tests/browser")
    run(npm, "run", "format:check")
    run(python, "-m", "ruff", "check", "musubi_gui", "tools", "tests/python", "tests/browser")
    run(npm, "run", "lint")
    run(python, "-m", "mypy")
    run(npm, "run", "typecheck")
    run(python, "-m", "bandit", "-r", "musubi_gui", "tools", "-c", "pyproject.toml", "-q")


def dependency_and_package_checks(python: str, npm: str) -> None:
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
            r'^\s*"(?:expected_upstream|upstream_revision|application_revision|header_sha256|checkpoint_sha256)": "[a-f0-9]{40}(?:[a-f0-9]{24})?",?$',
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


if __name__ == "__main__":
    main()

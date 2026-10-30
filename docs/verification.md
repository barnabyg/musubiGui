# Verification

Prerequisites: native Windows 11, Python 3.12, uv, Node.js 24 LTS and Microsoft Edge.
Run `uv sync --locked` and `npm.cmd ci` to prepare the isolated app environment.
The training environment is independent and is never installed or upgraded by these commands.

The canonical command is `npm.cmd run verify`. It checks Python/TypeScript formatting,
lint, strict types, Bandit static security analysis, pytest and real Edge browser tests,
lockfile consistency, installed npm dependency integrity, Python/npm vulnerabilities,
detect-secrets, and sdist/wheel packaging. The browser suite builds the UI before testing.
CI uses the same command. When it fails, CI keeps Playwright's `test-results`
(page snapshots and failure screenshots of synthetic fixtures) as the
`playwright-results` artifact for 14 days. Registry access is required by vulnerability and lock checks.
All diagnostics and warnings must be resolved; pytest warnings and ESLint warnings fail.

Browser assertions have a 15-second integration deadline because they exercise real
backend requests, SQLite writes and media subprocesses on hosted Windows runners.
Controlled six-second dataset creation and completed-scan responses reproduce failures
under Playwright's five-second default and pass with this budget. This is a correctness
suite, not a latency benchmark: assertions still require the exact expected result.
Long upload/scan/preparation/training operations use explicit 45-second waits. This
includes four-media registration scans: an 18-second completed-scan response reproduces
the 15-second failure and passes with the operation budget. Managed dataset
creation also scans before responding and uses the same deadline; two consecutive
hosted runs timed out at 15 seconds, and an 18-second creation response passes.
The two-family preparation/training scenario has a 120-second total budget.
H3 media inspection results use a 120-second wait inside a 1,200-second test budget,
because each of the scenario's inspections decodes whole clips in new subprocesses: hosted
runs exceeded both the 15-second and 60-second defaults while the test takes 10 seconds
locally. The pytest wait for two queued fixture preparations uses 180 seconds. These are upper
bounds with deliberate margin, and a test budget must exceed the sum of its waits.
The gallery/inline-caption virtual scrolling fixtures use eight real media files and
a narrow viewport that forces cards to unmount. This tests recycling and retained
drafts without requiring 24 separate probe startups just to exceed a desktop
viewport. Both scenarios allow 90 seconds overall and retain the standard
45-second scan deadline. Completion assertions target the visible scan status so a
failure includes its last reported progress or error. The earlier 24-media fixtures
exceeded even a 90-second scan budget on one hosted runner, while another passed;
increasing timeouts alone did not make that unrelated setup workload reliable.
Other fixture deadlines and the 15-second default remain unchanged. Retries remain
disabled. Review these deadlines if fixture workloads or runner capacity change.

Focused commands: `npm.cmd run typecheck`, `.venv\Scripts\python.exe -m mypy`,
`.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py -q`, and
`npm.cmd run build && npm.cmd run test:browser` (in cmd.exe).

Exceptions: subprocess calls have narrow Bandit `B603` annotations beside their fixed
argument arrays or explicitly selected training executable. They never use a shell.
The `B404` import exception permits those same controlled subprocess operations.
Revisit these if command sources change. Missing Torch in the application environment is expected;
the probe imports Torch only through the selected existing training Python.

The detect-secrets allowlist marks the public upstream Git revision in `reference.py`, and
the public Hub commit and tokenizer file hashes in `wan_tokenizer.py`.
An exact JSON-field regex also exempts 40/64-character hexadecimal `expected_upstream` / `upstream_revision`,
`application_revision` and `header_sha256` values in reference evidence: these are public Git revisions/content hashes,
not credentials. Re-review both exceptions whenever the identity schema or recipe pin changes.
The scanner checks tracked and new
non-ignored repository files. Generated bundles and local training assets are ignored,
so minified third-party strings cannot be mistaken for project credentials.

GPU reference execution is separate from CI and ordinary verification. Fake processes
and fixture checkout files prove orchestration and persistence, not model support.

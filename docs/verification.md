# Verification

Prerequisites: native Windows 11, Python 3.12, uv, Node.js 24 LTS and Microsoft Edge.
Run `uv sync --locked` and `npm.cmd ci` to prepare the isolated app environment.
The training environment is independent and is never installed or upgraded by these commands.

The canonical command is `npm.cmd run verify`. It checks Python/TypeScript formatting,
lint, strict types, Bandit static security analysis, pytest and real Edge browser tests,
lockfile consistency, installed npm dependency integrity, Python/npm vulnerabilities,
detect-secrets, and sdist/wheel packaging. The browser suite builds the UI before testing.
CI uses the same command. Registry access is required by vulnerability and lock checks.
All diagnostics and warnings must be resolved; pytest warnings and ESLint warnings fail.

Focused commands: `npm.cmd run typecheck`, `.venv\Scripts\python.exe -m mypy`,
`.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py -q`, and
`npm.cmd run build && npm.cmd run test:browser` (in cmd.exe).

Exceptions: subprocess calls have narrow Bandit `B603` annotations beside their fixed
argument arrays or explicitly selected training executable. They never use a shell.
The `B404` import exception permits those same controlled subprocess operations.
Revisit these if command sources change. Missing Torch in the application environment is expected;
the probe imports Torch only through the selected existing training Python.

The detect-secrets allowlist marks the public upstream Git revision in `reference.py`.
An exact JSON-field regex also exempts 40/64-character hexadecimal `expected_upstream` / `upstream_revision`
and `header_sha256` values in reference evidence: these are public revisions/content hashes,
not credentials. Re-review both exceptions whenever the identity schema or recipe pin changes.
The scanner checks tracked and new
non-ignored repository files. Generated bundles and local training assets are ignored,
so minified third-party strings cannot be mistaken for project credentials.

GPU reference execution is separate from CI and ordinary verification. Fake processes
and fixture checkout files prove orchestration and persistence, not model support.

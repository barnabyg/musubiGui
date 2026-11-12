# Verification

Prerequisites: native Windows 11, Python 3.12, uv, Node.js 24 LTS and Microsoft Edge.
Run `uv sync --locked` and `npm.cmd ci` to prepare the isolated app environment.
The managed training environment is independent and is never installed or upgraded by these verification commands. Settings installs/updates it explicitly.

The canonical command is `npm.cmd run verify`. It checks Python/TypeScript formatting,
lint, strict types, Bandit static security analysis, pytest and real Edge browser tests,
lockfile consistency, installed npm dependency integrity, Python/npm vulnerabilities,
detect-secrets, and sdist/wheel packaging. `npm.cmd run test:browser` builds the UI from the
current `frontend/` source before testing, through its `pretest:browser` script, including when
it is given a single spec file. Running `playwright test` directly skips that build.
`tests/browser/api.spec.ts` is the exception to real browser testing: it runs the shared
request helper in Node with a stubbed `fetch`, to reach error responses the backend doesn't produce.
Python that browser tests run lives in `tests/browser/helper.py`, under the same format, lint and type checks.
CI uses the same command. When it fails, CI keeps Playwright's `test-results`
(page snapshots and failure screenshots of synthetic fixtures) as the
`playwright-results` artifact for 14 days. Registry access is required by vulnerability and lock checks.
All diagnostics and warnings must be resolved; pytest warnings and ESLint warnings fail.

When `CI` is set, pytest and Playwright each stop at their first failure, so a failing run
ends soon after its first useful signal. Local runs still report every failure. The Verify
job has a 120-minute limit, well above the slowest passing hosted run of about 18 minutes.
It runs for pull requests and pushes to `main`. A branch without a pull request can run it
by hand with `gh workflow run Verify --ref <branch>`.

Pytest and Playwright each run in parallel: four workers locally and two when `CI` is set.
Each test owns its temporary state, backend port and GPU lease file. The focused pytest
command below runs serially unless given `-n`.

Test deadlines are upper bounds, not targets, so they carry a lot of margin. A passing test
never waits them out, while a tight deadline fails correct code when a hosted Windows runner
crawls. Hosted runs have taken over 45 seconds to scan four media files, and over 60 seconds
for an H3 inspection scenario that takes 10 seconds locally. Deadlines are never set just
above an observed time.

Browser assertions wait up to 60 seconds, because they exercise real backend requests,
SQLite writes and media subprocesses. Long upload, scan, inspection, preparation and training
operations use explicit 240-second waits. Each test has a single 30-minute budget, which
exceeds the sum of its waits. Pytest polls for Run states for up to 180 seconds, and its
subprocesses and requests have 300 seconds. The cost is that a truly hung test takes longer
to fail, up to the job limit in CI. This is a correctness suite, not a latency benchmark: assertions still require the
exact expected result.

The gallery/inline-caption virtual scrolling fixtures use eight real media files and
a narrow viewport that forces cards to unmount. This tests recycling and retained
drafts without requiring 24 separate probe startups just to exceed a desktop
viewport. Completion assertions target the visible scan status so a
failure includes its last reported progress or error. Retries remain disabled.

Focused commands: `npm.cmd run typecheck`, `.venv\Scripts\python.exe -m mypy`,
`.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py -q`, and
`npm.cmd run test:browser -- tests/browser/settings.spec.ts`.

To run one test case:
`.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py::test_browser_cannot_change_settings_from_another_origin -q` and
`npm.cmd run test:browser -- tests/browser/settings.spec.ts -g "diagnostic rows share one layout"`.

The client-only Vite build omits Rolldown's `MODULE_LEVEL_DIRECTIVE` diagnostic
for the `"use client"` directive in Lucide's `context.mjs` and `Icon.mjs`.
Revisit this exception if the app adds server rendering or changes Lucide versions.

Exceptions: subprocess calls have narrow Bandit `B603` annotations beside their fixed
argument arrays or explicitly selected training executable. They never use a shell.
The `B404` import exception permits those same controlled subprocess operations.
Revisit these if command sources change. Missing Torch in the application environment is expected;
the probe imports Torch only through the managed training Python.

The detect-secrets allowlist marks the public upstream Git revision in `recipe.py`, and
the public Hub commit and tokenizer file hashes in `wan_tokenizer.py`, and the fixed stage-key
digests of made-up inputs in `test_job_datasets.py`, and the fixed digest of made-up H3 arguments in
`test_training_sections.py`. Neither is a credential; review them if the digest inputs change.
An exact JSON-field regex also exempts 40/64-character hexadecimal `expected_upstream` / `upstream_revision`,
`application_revision`, `header_sha256` and `checkpoint_sha256` values in reference evidence: these are public Git revisions/content hashes,
not credentials. Re-review both exceptions whenever the identity schema or recipe pin changes.
The scanner checks tracked and new
non-ignored repository files. Generated bundles and local training assets are ignored,
so minified third-party strings cannot be mistaken for project credentials.

GPU reference execution is separate from CI and ordinary verification. Fake processes
and fixture checkout files prove orchestration and persistence, not model support.

Managed checkout tests use local synthetic Git repositories. Browser tests exercise the real
install/update API, Git checkout, isolated Python creation, reload/restart, dirty-source
refusal and retry. They substitute dependency downloads with an isolated `uv venv`; they do not qualify
CUDA dependencies or GPU/model support. The managed training environment must be qualified
separately using the GPU reference workflows before claiming new GPU support.

Manual managed-checkout verification: from the repository root, run `./launch.cmd`.
In Settings, click Install supported version (or Update to supported version), wait for
completion, and confirm the installed and supported revisions match. Reload and confirm
it stays installed, then run diagnostics. Prerequisites are Git, uv, Python 3.12, internet
access and enough disk space for the separate CUDA training environment. Existing models
are needed for GPU diagnostics/training, not for the source installation itself.

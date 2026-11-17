# Verification

## Prerequisites

Native Windows 11, Python 3.12, uv, Node.js 24 LTS and Microsoft Edge. Run `uv sync --locked`
and `npm.cmd ci` to prepare the isolated app environment.

## Canonical command

`npm.cmd run verify` checks everything; see [what it runs](#what-verify-runs).

## Focused commands

For one file:

- `npm.cmd run typecheck` and `.venv\Scripts\python.exe -m mypy`
- `.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py -q`
- `npm.cmd run test:browser -- tests/browser/settings.spec.ts`

For one test case:

- `.venv\Scripts\python.exe -m pytest tests/python/test_settings_api.py::test_browser_cannot_change_settings_from_another_origin -q`
- `npm.cmd run test:browser -- tests/browser/settings.spec.ts -g "diagnostic rows share one layout"`

`npm.cmd run test:browser` builds the UI from the current `frontend/` source before testing,
through its `pretest:browser` script, including when it is given a single spec file. Running
`playwright test` directly skips that build. The focused pytest command runs serially unless
given `-n`.

Browser specs are named in kebab-case, `tests/browser/<feature-name>.spec.ts`.

## What verify runs

The canonical command checks Python/TypeScript formatting, lint, strict types, Bandit static
security analysis, pytest and real Edge browser tests, lockfile consistency, installed npm
dependency integrity, Python and production npm vulnerabilities, detect-secrets, and sdist/wheel
packaging. The npm audit skips dev dependencies, which build the UI locally and are not shipped.
Registry access is required by vulnerability and lock checks. All diagnostics and warnings
must be resolved; pytest warnings and ESLint warnings fail.

The managed training environment is independent and is never installed or upgraded by these
verification commands. Settings installs and updates it explicitly.

`tests/browser/api.spec.ts` is the exception to real browser testing: it runs the shared
request helper in Node with a stubbed `fetch`, to reach error responses the backend doesn't
produce. Python that browser tests run lives in `tests/browser/helper.py`, under the same
format, lint and type checks.

Managed setup tests check the pinned Triton wheel command, retry after dependency failure,
and migration of an old readiness marker. Managed checkout tests use local synthetic Git
repositories. Browser tests exercise the real install/update API, Git checkout, isolated
Python creation, reload/restart, dirty-source refusal and retry. They substitute dependency
downloads with an isolated `uv venv` and use a synthetic installer to check that missing H3
acceleration appears in diagnostics. They do not validate GPU kernels or qualify CUDA
dependencies or GPU/model support. Qualify the managed training environment separately with
the [GPU reference workflows](reference/README.md) before claiming new GPU support.

GPU reference execution is separate from CI and ordinary verification. Fake processes and
fixture checkout files prove orchestration and persistence, not model support.

The gallery/inline-caption virtual scrolling fixtures use eight real media files and a narrow
viewport that forces cards to unmount. This tests recycling and retained drafts without
requiring 24 separate probe startups just to exceed a desktop viewport. Completion assertions
target the visible scan status so a failure includes its last reported progress or error.

## Parallel runs and CI

Pytest and Playwright each run in parallel: four workers locally and two when `CI` is set.
Each test owns its temporary state, backend port and GPU lease file.

CI runs the canonical command in nine parts on separate Windows runners at once.
`npm.cmd run verify -- static` runs everything except the two test suites, then builds the UI
for packaging, and `npm.cmd run verify -- python --shard 2/4` or `-- browser --shard 2/4` runs
one quarter of a suite. With no part it runs every check. A shard of either suite deals the
tests out in turn, so each file's tests spread across all four. The slow Run and preview specs
sit together in test order, so a contiguous split ran one browser shard twice as long as
another. Pytest gets this from `--shard` in `tests/python/conftest.py`, which needs the
`tests/python` path on the pytest command line to load the option before arguments are parsed.
Browser shards pass Playwright a `--test-list` that `verify.py` writes under `.cache`;
Playwright's own `--shard` takes a contiguous run.

Verify runs for pull requests and pushes to `main`. A branch without a pull request can run
it by hand with `gh workflow run Verify --ref <branch>`.

When `CI` is set, pytest and Playwright each stop at their first failure, so a failing run
ends soon after its first useful signal, and the first failing part cancels the other parts.
Local runs still report every failure. Retries remain disabled, so a trace belongs to the
original failure.

When a browser part fails, CI keeps Playwright's `test-results` (traces, page snapshots,
failure screenshots and per-backend logs of synthetic fixtures) as a `playwright-results-<n>`
artifact for 14 days. Successful tests discard their diagnostic output. Traces record browser
actions, DOM snapshots, console messages and network requests/responses; backend logs survive
fixture cleanup and include startup, stdout/stderr and exit details, including across backend
restarts. Download and extract the artifact, then run
`npx.cmd playwright show-trace <path-to-trace.zip>` from the repository root to inspect the
action and network history around a failure.

Every test part, passing or not, also keeps a `test-durations-<n>` artifact for 14 days:
pytest's JUnit XML or Playwright's JSON report, giving each test's duration on the hosted
runner for balancing shards. `<n>` is the part's matrix position: 1–4 are the pytest shards and
5–8 the browser shards.

## Timeouts

Test deadlines are upper bounds, not targets, so they carry a lot of margin. A passing test
never waits them out, while a tight deadline fails correct code when a hosted Windows runner
crawls. Hosted runs have taken over 45 seconds to scan four media files, and over 60 seconds
for an H3 inspection scenario that takes 10 seconds locally. Deadlines are never set just
above an observed time.

- Each Verify part in CI has a 120-minute limit, far above the slowest passing hosted part of
  about 6 minutes and the 27-minute single job the parts replaced.
- Each browser test has a single 30-minute budget, which exceeds the sum of its waits.
- Browser actions and navigation each have a 240-second limit, so a missing option or control
  cannot consume the entire test budget. This leaves headroom for slow hosted uploads and
  native folder-picker interactions.
- Browser assertions wait up to 60 seconds, because they exercise real backend requests,
  SQLite writes and media subprocesses. Long upload, scan, inspection, preparation and training
  operations use explicit 240-second waits.
- Pytest polls for Run states for up to 180 seconds, and its subprocesses and requests have
  300 seconds.
- Recovery tests keep synthetic processes alive until explicitly released or terminated,
  rather than assuming assertions finish within a short sleep. They wait for startup evidence
  before restarting the service and retain a 900-second emergency deadline to bound leaked
  fixtures.

The cost is that a truly hung test takes longer to fail, up to the job limit in CI. This is a
correctness suite, not a latency benchmark: assertions still require the exact expected
result.

## Exceptions

- The client-only Vite build omits Rolldown's `MODULE_LEVEL_DIRECTIVE` diagnostic for the
  `"use client"` directive in Lucide's `context.mjs` and `Icon.mjs`. Revisit this if the app
  adds server rendering or changes Lucide versions.
- Subprocess calls have narrow Bandit `B603` annotations beside their fixed argument arrays or
  explicitly selected training executable. They never use a shell. The `B404` import
  exception permits those same controlled subprocess operations. Revisit these if command
  sources change.
- Missing Torch in the application environment is expected; the probe imports Torch only
  through the managed training Python.
- The detect-secrets allowlist marks the public upstream Git revision in `recipe.py`, the
  public Hub commit and tokenizer file hashes in `wan_tokenizer.py`, the fixed stage-key
  digests of made-up inputs in `test_job_datasets.py`, and the fixed digest of made-up H3
  arguments in `test_training_sections.py`. None is a credential; review them if the digest
  inputs change.
- An exact JSON-field regex also exempts 40/64-character hexadecimal `expected_upstream` /
  `upstream_revision`, `application_revision`, `header_sha256` and `checkpoint_sha256` values
  in reference evidence: these are public Git revisions/content hashes, not credentials.
  Re-review both detect-secrets exceptions whenever the identity schema or recipe pin changes.

The secrets scanner checks tracked and new non-ignored repository files. Generated bundles and
local training assets are ignored, so minified third-party strings cannot be mistaken for
project credentials.

## Manual scenarios

Feature docs give their own manual scenarios. Managed setup is described here only.

### Managed setup

Prerequisites: Git, uv, Python 3.12, internet access and enough disk space for the separate
CUDA training environment. Existing models are needed for GPU diagnostics and training, not
for the source installation itself.

From the repository root in PowerShell 7, run:

```powershell
.\launch.cmd
```

1. In Settings, click **Install supported version** (or **Update to supported version**),
   wait for completion, and confirm the installed and supported revisions match.
2. Configure storage and model paths, save, then choose **Run diagnostics**. Expect the
   Triton check to pass.
3. Click **Update to supported version** again to confirm setup is repeatable; the installed
   revision should still match the supported revision.
4. Reload and confirm it stays installed. Stop the backend, relaunch, and confirm that the
   fields persist.
5. Change a field without saving: diagnostics require a save, and closing the tab prompts
   about unsaved edits. A failed save retains edits and reports the failure.
6. Prepare and start a short H3 Run using configured models and a compatible dataset. Expect
   training to complete without the ConvRot transient-dequantization fallback warning.

Training quality and speed require separate GPU measurements.

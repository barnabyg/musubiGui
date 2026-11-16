# Runs

Use **Runs** in the sidebar to browse Run history and manage the pending queue. Select a Run to open its dedicated detail page; **Back to runs** returns to history. Prepare new Runs from a saved, reviewed job using the link to Jobs. Direct links, reloads and browser back/forward keep the selected detail page.

The Runs collection lists the queue and history. Opening a Run shows only its details. Statuses read as labels such as
Preparing or Ready to start, and the stored values are unchanged. The GPU line names the job
using the GPU and links to its Run. On Run details it shares one line with the reminder that
closing the browser leaves training running.

Each Run opens on **Overview**, a flat stack of panels: the Run summary (state, timing,
progress, warnings and the originating job), Actions with the stage stepper, Resume
training, Caching, Checkpoints, Compare with another Run, Inputs and output folder, and
Stage logs. **Samples** holds only generated media and periodic previews. Use the tabs
or arrow keys to switch views. Switching views keeps the selected sample and its
player mounted, so returning preserves playback. New previews do not replace a
selected sample. Take a LoRA from the Checkpoints panel or straight from the Run's
output folder.

New output and cache folders use the Job name and a UTC timestamp, for example
`My-Job-2026-11-12_12-30-00Z`. Retry folders include `retry` before the timestamp.
Restored segments use `restore` in their output folder and share their source's cache.
Same-name Runs created in the same second get
numeric suffixes (`-2`, `-3`, …). Existing folders keep their paths; internal Run
IDs and cache fingerprints remain independent. Checkpoint files and saved-state folders
use the Job name and revision, for example `My-Job-r2.safetensors` and `My-Job-r2-state`.

Alternating Wan adds the expert and its cumulative completed optimizer updates:
`My-Job-r2-low-step00000100.safetensors` and
`My-Job-r2-high-step00000100.safetensors` each contain 100 updates for that expert.
Interval saves and restored segments use the same count, including updates made before
restoration. Saved-state folders use the matching stem with `-state`. Download names
retain the expert and step (without zero padding). Existing Runs keep their frozen names,
including older `phase00000000` identifiers that record the phase's schedule start;
they remain downloadable and restorable. Newly prepared Runs and restored segments use
completed expert steps.

To check Wan checkpoint naming, run this command from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/wan-checkpoint-names-$([guid]::NewGuid().ToString('N'))"
```

This isolated CPU fixture needs Windows, Node.js/npm, uv and Edge; no GPU or models.
Open **WAN acceptance 1**, set Training steps to 4, steps per expert before switching
to 2, and Save every N steps to 1. Save, review, then **Prepare and start**. In the
Run's output folder, expect each expert's checkpoints and state folders at steps 1,
2, 3 and 4; the paired downloads should end in `-low-step4.safetensors` and
`-high-step4.safetensors`. Inspect saved states and restore the pair at step 2 for two
additional steps: the new segment's final files should both end in `-step00000004`.
GPU training quality is outside this naming check.

To check folder naming, from the repository root run
`.\acceptance.cmd --workspace ".cache/folder-check-$([guid]::NewGuid().ToString('N'))"`.
Prepare a fixture Job and inspect the Output path on its Run. It should contain
the Job name and timestamp, without a random suffix. The fixture needs no models
or GPU; preparation creates synthetic training inputs. Start training, download the final
LoRA from Checkpoints and inspect the Run's `checkpoints` folder. Checkpoint files and state folders should
use the frozen Job name and revision, with no random job ID. Rename the Job and confirm
the original Run still downloads and offers its saved states.

## History refresh and network traffic

The progress area shows **Seconds per step**, using the latest training progress
bar's reported rate. Rates reported as iterations per second are converted to
seconds per step. This is the trainer's smoothed rate, not a measured individual
GPU operation. It stays unknown when no rate was reported, including CPU fixtures.
It also works for existing Runs whose retained training log contains a rate.

## H3 performance diagnostics

New H3 stages automatically append `performance.jsonl` in the Run's output folder.
On Overview, **Download performance diagnostics** downloads a snapshot of its
complete JSON lines. Existing Runs without a report remain readable; diagnostics
cannot reconstruct past hardware measurements. Restart the application to use the
collector on subsequently launched stages. A Run already training in an older
backend does not acquire the collector retroactively.

The report records UTC timestamps, stage elapsed time, frozen configuration,
executed arguments and provenance at each stage start. Every approximately five
seconds during caching, training and generation it records:

- NVIDIA device identity, utilization, VRAM used/total, power draw/limit,
  temperature, graphics/memory clocks and performance state, for every visible
  device returned by `nvidia-smi`. CUDA device visibility is recorded at stage start.
- Available host RAM and memory usage percentage, process-tree RSS, CPU usage and
  read/write byte rates. CPU percentage can exceed 100% across cores. Process I/O
  counters measure logical I/O, not physical disk throughput. RSS can double-count
  shared pages; host usage and device measurements include unrelated applications.
- Latest training step, elapsed time, ETA, loss and reported seconds per step.
  Observed seconds per step measures intervals between changes seen by the collector,
  divided by the number of steps advanced. It includes intervening saves/previews,
  can combine several steps and has roughly five-second observation granularity.
  It remains unknown until two different step positions have been observed.

Stage launch/loading time remains separate from observed step intervals; a step-zero
to step-one interval can include startup. A final progress observation and monitor
stop record preserve the last position and launcher exit code. `null` means unknown,
not zero. Missing GPU utilities, unsupported fields, access errors and timed-out
queries are recorded explicitly. CPU fixtures disable GPU queries and identify
themselves as synthetic; they are not training-performance evidence.

The collector runs outside the trainer, imports no Torch and adds no CUDA
synchronization. It does not change model arguments or enable a profiler. Queries
have a ten-second timeout, diagnostics stop with their stage/service, and diagnostic
write failures do not fail training. Sampling and file writes still have some overhead;
`collection_seconds` helps identify expensive probes. Measurements can miss short
spikes. The report does not time individual forward/backward passes, attention
kernels or PCIe transfers, and cannot prove the cause of a slowdown by itself.
Credentials are redacted before writing, but the report contains local paths and
job settings; review these before sharing it.

`GET /api/runs/{id}/logs` adds `performance_available` and nullable
`progress.seconds_per_step`. `GET /api/runs/{id}/performance` downloads the report,
or returns 404 when none exists. No existing persisted Run format is replaced.

Manual verification: in PowerShell 7, from `C:/docs/git/musubiGui`, run:

```powershell
.\launch.cmd
```

Prerequisites: configured training installation, H3 models, prepared videos and an
NVIDIA GPU. Start an H3 Run and open Overview. After training reports a speed, expect
**Seconds per step** to match the latest `steps:` log bar. Download diagnostics during
training and after completion; each line should parse as JSON and include stage/sample
records. Compare GPU utilization, power and available RAM with Task Manager or
`nvidia-smi`, allowing for sampling delay and their different memory definitions.
Reload Overview and expect the download and reported speed to remain available.

## Remaining-time estimates and refresh

**Run elapsed** measures execution start to now while work is active, and freezes
at the Run's recorded completion, stop, cancellation or failure time. It includes
model loads/unloads, expert transitions, saves, periodic previews and final sampling;
it keeps advancing after the last training phase finishes. Initial queue waiting is
excluded. **Prepare and start** starts the clock when preparation leaves the queue,
so its caching belongs to that execution. With **Prepare**, followed later by
**Start training**, the clock starts when training leaves the queue; separate
preparation and the wait for Start are excluded. A restored segment or retry has
its own clock and never inherits its source's elapsed time.

The execution start is stored as nullable `execution_started` in each new Run;
`finished` remains its terminal timestamp. These timestamps survive browser reloads
and backend restart. An interrupted execution whose actual end was not observed
shows unknown, as do uncertain/orphaned processes; recovery time is not presented
as the actual finish time. Recovered stops carry `execution_end_unknown: true`:
their `finished` value records reconciliation, because the actual process exit
was not observed, so their elapsed value also stays unknown. Restored segments
clear that marker. Cancelled-before-start Runs show not started. Existing
records remain readable: when the execution-start field is absent, the UI labels
its fallback to the first recorded training start as legacy. It cannot reconstruct
earlier preparation. Missing or invalid timestamps stay unknown.

Manual timing check, from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/run-timing-$([guid]::NewGuid().ToString('N'))"
```

Prerequisites: Windows, Node.js/npm, uv and Microsoft Edge; no GPU or models are
needed for this isolated synthetic workspace. Enable previews on a fixture Job,
use several steps and **Prepare and start**. Compare Run elapsed with a stopwatch
from the Preparing state, reload during training/preview and final sampling, and
confirm elapsed advances through the whole execution then stays fixed. Prepare a
different revision without starting, wait, then Start: that Run's clock should
exclude the wait and its earlier caching. CPU fixtures do not measure GPU speed.

Training progress includes **Estimated training remaining to max steps**. It
extrapolates the trainer's observed step speed over all remaining steps, including
later alternating Wan phases and the requested steps of a restored segment.
It stays unknown until timing is available. This is a training estimate, not a
finish-time promise: future model loading, previews and final sampling are excluded,
and expert speeds may differ. The trainer's own Training ETA or Phase ETA remains
visible separately. The logs API adds nullable `estimated_remaining_seconds`.

**Estimated Run remaining** and **Estimated finish** are separate forecasts. New
Runs observe native Wan/H3 previews from before the trainer's sampling setup hook
through its return-to-training cleanup hook, including inference, decoding and
saving. Standalone paired-Wan previews use the whole generation-stage wall time,
including process/model loading and expert transitions. Sampling measurements are
retained in each Run's `timing-*.json` observations and stage timestamps, so reloads,
reopening, backend restart and expert switches keep them. Existing frozen commands
are unchanged; missing historical native timings cannot be reconstructed.

The forecast uses the mean completed duration for comparable native previews or
standalone generation, separately. It counts outstanding work from the frozen stage
schedule, including per-expert Wan and restored segments, and subtracts actual
completions. A final-boundary periodic preview and a separately requested final
sample are separate operations. For example, one 10-minute preview with nine
remaining adds 90 minutes, plus remaining training and measured overhead. Subsequent
completions refine the mean. An active preview's elapsed portion is subtracted from
its budget, clamped at zero; overruns are identified rather than producing negative
remaining time. Terminal/stopping/interrupted Runs have no future finish prediction.

Step intervals are measured outside the preview hooks and preview wall time is
removed before extrapolation. The upstream progress bar can include previews and
reset its timing; it is never used as a preview-free rate for this whole-Run forecast.
Observed inter-step saves remain in the step rate. Completed phases can supply
startup and final-save overhead for later comparable phases, without adding their
first update twice. A phase that is still loading retains its measured startup
budget, less time already spent loading, until its first update. An unmeasured expert uses the observed expert's rate with an
explicit assumption until it reports its own measurements. Forecasts identify
unestimated components such as future loading/switching and final state saves;
they are partial estimates, not finish-time promises. Before step or preview
measurements exist, the affected budget and whole-Run finish remain unknown.
When only part of the work is measured, Overview shows the outstanding preview
budget and a **Partial remaining estimate**, with a conditional finish for those
estimated components. Unmeasured work, including a separate final generator, is
listed as excluded/assumed. This is a partial prediction, not a guaranteed lower
bound. The full remaining/finish values stay unknown until their components have
measurements. Native
and standalone samples use separate timing pools because their residency costs
differ; a native preview cannot qualify a separate final generator's budget.

`GET /api/runs/{id}/logs` adds `run_estimate`, including nullable
`remaining_seconds`, `finish_at`, training/preview/final-sampling budgets, observed
`accounted_remaining_seconds`/`accounted_finish_at` for a partial forecast,
overhead, outstanding/completed preview counts, and `unknown_components`. It
refreshes while training/generation is active even when a training phase has ended.
Persisted Jobs, exports and existing Runs remain readable. New native training
commands use an app-owned wrapper around the pinned trainer's existing hooks;
it changes no training/sampling arguments or model residency. CPU fixtures exercise
the same timing storage, but their durations are not GPU measurements.

For manual forecast verification use the isolated launch command above. Set a
fixture Job to at least 20 steps and preview every 2, disable final sampling, then
Prepare and start. Expect the Run forecast to appear after step and first-preview
measurements; its outstanding count should fall after each preview. Reload during
training and confirm the count/estimate survive. The fixed 10-minute/nine-preview
case is covered deterministically through the API rather than making a fixture
sleep for ten minutes. For a real-GPU stopwatch check, use `.\launch.cmd` with
configured models and a dataset, and allow for unestimated overhead and variable
expert speed.

Manual check from the repository root: run `.\launch.cmd`, start a prepared Job
with several training steps and confirm the estimate appears once step timing is
reported. For alternating Wan, confirm it covers the total remaining steps while
Phase ETA covers only the current phase. Requires installed training dependencies,
models and a dataset; the CPU fixture has no timing and should show unknown.
Its upstream Training/Phase ETA stays unknown; its separate Run forecast uses
synthetic wall-clock observations.

History, queue updates and completion notifications share lightweight Run summaries.
The selected Run's frozen inputs are loaded separately, again only when its recorded
details change or after an action. Action responses select the Run; a fresh detail
request keeps a delayed response from replacing newer live progress. Logs refresh while work is running or needs recovery; a terminal Run's
logs are fetched once on selection or a status transition. Slow responses finish before
the next poll is scheduled, and leaving a selection cancels its outstanding requests.
Failed detail/log requests retry after the response completes, even for an unchanged
terminal Run. A successful retry clears that read's error and stops terminal log
refreshes; unrelated action and capability errors remain visible.
Output lists still refresh at stage transitions and through Refresh outputs.

`GET /api/runs/summaries` returns Run ID, creation time, status, queue order, job
identity, stage names/statuses and an opaque `change_key` for invalidating selected
detail. It excludes frozen manifests, job configuration, settings, stage arguments
and provenance. The existing `GET /api/runs` and `GET /api/runs/{id}` retain their full
responses for API callers. Persisted Run formats are unchanged.

In the isolated CPU fixture on 9 November 2026, a 3.25-second Edge observation with
four completed Runs transferred five detailed history responses of 54,969 bytes each
before this change (274,845 bytes, excluding logs/capabilities). Afterward, the same
window transferred three summaries totalling 5,907 bytes and three capability responses
totalling 420 bytes, with no repeated detail or terminal logs. With a fifth active Run
while the completed Run stayed selected, three summary responses totalled 7,370 bytes
and capabilities totalled 2,014 bytes; no unrelated detail or logs were fetched.
These are uncompressed response-body bytes for synthetic single-item Runs, not a
large-library latency benchmark. A response delayed by 1.5 seconds kept at most one
history request in flight.
Observation windows count requests when they start, then wait for those responses to
finish measuring their bodies. A delayed response from opening the selected Run belongs
to that initial load, even if its body arrives during a later idle window.

To inspect this manually, run from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Complete both model jobs, leave one finished Run open with Stage logs collapsed, and
inspect Edge's Network panel. Only summaries and capabilities should keep refreshing.
Start another Run from a saved revision: history and the completion title should update
without repeatedly transferring the finished Run's inputs or logs. Select the active
Run to see live step/loss updates and its outputs on completion. Refresh outputs should
preserve a playing sample.

## Preparation

### Alternating Wan runs

Alternating Wan training is one Run and one GPU queue entry, with sequential low/high
training phases inside it. Dataset caching runs once. Each phase keeps its own logs,
checkpoint and optimizer state; neither expert is kept resident while the other trains.
The GPU lease stays owned across phase switches and final paired sampling.
Progress reports the active expert, phase updates, overall segment updates and both
experts' cumulative updates. **Phase ETA** retains the upstream estimate for the active
expert phase; **Training ETA** applies to single-process training. Neither includes
later sampling. Missing upstream estimates stay unknown. **Training elapsed** uses
recorded timestamps from the first training stage through the latest training stage,
including expert-switch overhead, rather than the resetting progress-bar clock. It
survives browser reloads and stops advancing when training ends.
The logs API keeps `progress.eta` in its upstream time format and adds
`progress.eta_scope: "phase"` for alternating Wan; callers must preserve that scope.

**Inspect saved states** lists paired schedule positions, including interval saves
partway through a phase. Restoration freezes both available expert states as a new
segment and continues the schedule at that position. For newly prepared per-expert Runs, additional steps extend both experts equally after
catching up an interrupted unmatched pair. Legacy Runs retain total updates
across both experts. An early save before high-noise first trains records high at zero;
high starts fresh when its phase arrives. Restored checkpoints include a local copy
of the inactive expert's source LoRA, so both downloads are available even if only one
expert trains in the new segment.

Stopping does not force a new save. Choose a complete saved schedule position and
expect any unsaved updates after that position to be lost. Restoration validates the
state files and their counters; missing or changed state blocks it. Retry after paired
training has started directs you to inspect/restore paired state rather than silently
restarting either expert. Preparing the Job again explicitly starts both experts over.
Restoration retains source dependencies; active/queued segments prevent deleting their
source Run. Musubi restores optimizer, scheduler and RNG but starts a fresh dataset
pass at each phase, including restored phases; exact batch position is not preserved.

Preparation reserves snapshot creation through a separate state-scoped SQLite gate.
Duplicate requests from tabs or backend processes return the existing Run before
allocating another media/cache directory. Content hashes, effective settings, source
digest and stable runtime versions identify the snapshot; changing media bytes creates
a new identity even before an index re-scan. A request waiting over 30 seconds reports
that another snapshot is being created and can be retried safely.

## Preparing an unchanged revision

Preparing an unchanged revision returns its existing Run, and the Jobs screen says
so and links to it. A Run that ended without completing (failed, cancelled,
stopped or interrupted) is not returned, unless it still records processes to
reconcile: Prepare creates a fresh Run of the same revision, for example after a
stop to change machine settings. Its caches are
reused through the stage fingerprints below, and the earlier Run keeps its logs,
outputs and saved states until you delete it. After Delete run, Prepare creates a
new Run of the same revision.

## Immutable inputs

New Run/output folders use the frozen Job name plus a UTC timestamp under the
configured output root's `runs` folder. Names replace Windows-invalid characters
and whitespace with hyphens, protect reserved names and limit the readable part
to 60 characters. Repeated Runs and same-name Jobs never share a folder. Review
shows `{timestamp}` until preparation allocates the folder; Run details show the
actual output path. Retries and restored segments also keep the frozen name.
Renaming a Job never moves older Runs, and existing folder layouts stay readable.

Before copying media, each new snapshot writes a versioned `snapshot.json` containing
its source manifest, settings and provenance. A directory with no published Run remains
an incomplete snapshot; caught failures mark it `incomplete` and retain the evidence.
The application preserves these directories for inspection rather than deleting their
contents automatically, and Delete run doesn't reach them because they have no Run.

Save and review a Job, then choose Prepare. Runs persist the exact job revision,
effective included media, caption text, generated dataset TOML, model identities,
upstream revision and selected Python/package environment. Source media and captions
are copied into a separate per-run directory under the configured output root.
Source captions remain editable while caching runs against the frozen copies.

`POST /api/runs/prepare` accepts `job_id`, optional `start`, and optional
`reviewed_revision` (the integer `revision` returned by Job review). The GUI sends
this revision for both prepare actions. A mismatch returns HTTP 409 before snapshot
creation or matching-Run reuse. Callers that omit it still prepare the latest saved
revision. Preparation always repeats backend validation.

## Caches

Preparation executes the pinned latent/text cache commands in the selected existing
training environment. Stage logs, exit codes, failures and cancellation survive
browser navigation. A zero exit without a cache for every included item is a failure.
Retries create a new run and keep the previous inputs and diagnostics until that Run is deleted.

Each stage fingerprints its dependencies. Caption changes invalidate text caches;
media or preprocessing changes invalidate latent caches. Cache reuse copies only
artifacts whose recorded content hashes still match. Changes to model files or
frozen inputs block execution and require a new preparation.

Cache provenance includes installed optional preprocessing versions (Pillow, NumPy,
tokenizers, sentencepiece, torchvision, torchaudio, SciPy and einops). Missing optional
packages remain recorded as absent and do not become installation errors. Before
launching each stage, a version-only selected-Python probe checks the recorded package
identity without importing Torch or initializing CUDA. Environment changes require a
new preparation; volatile GPU availability/free-space readings do not invalidate caches.

## Hugging Face Hub access

Wan text caching and generation need the UMT5 tokenizer from the Hugging Face Hub. The
first Wan Run downloads it and needs Hub access. Later Wan Runs use a verified shared
copy under the cache root and run with Hub access off, as
[Wan network access](reference/wan.md#network-access) describes. H3 still downloads its
processor, config and tokenizer into each Run's cache, so every H3 Run needs Hub access.

## GPU lease

A transactional host-wide GPU lease serializes execution, including instances with
different application state directories. Pending work and history live in versioned
SQLite storage. Unknown storage versions are preserved and blocked.

## Launching and target hardware

Run `launch.cmd` to open the local application. The release acceptance command and
synthetic media scenarios are documented with the complete release workflow.
Synthetic subprocess tests exercise orchestration, never model-support evidence.
The README lists the [target hardware](../README.md#target-hardware).

## Training

Choose Start training on a prepared Run. Its process runs independently of the
browser. The job can be edited while the Run retains its original revision. Repeated
Start requests select the same Run. Nonzero exits and missing checkpoints are explicit
failures. Checkpoint/sample locations stay associated with the originating Run.

The top of a Run shows its stages as Caching, Training, Sample and Done, each marked
done, in progress, not started, failed or stopped. Below them, Training shows the latest
step count, elapsed time, ETA and loss from upstream's progress bar. Unreported
values show as unknown. Training elapsed is explicitly scoped to the span of training
stages; Run elapsed above it covers the full execution. A separately labelled estimate extrapolates the observed speed to
maximum steps as described above. A restored segment counts its own steps, because
upstream resets the displayed counter. Stage logs are collapsed below that, and open by
themselves when a Run has failed.

## Prepare and start

Prepare and start on a reviewed Job prepares a Run and then trains it with no further
click. The request is stored with the Run, so it continues with the browser closed and
across a backend restart. It joins the same queue as Add to queue, so it waits its turn
if the GPU is busy. If preparation fails or is cancelled, training doesn't start. Used
on a revision that already has an unfinished Run, it asks that Run to continue instead
of creating another.

While the app is open on any screen, the tab title announces when a Run is ready,
finished, stopped or failed, until you return to the tab or change screen. The Runs
screen also offers an opt-in browser notification for the same events; the choice is
kept in that browser only.

## Links and browser history

The address bar names the screen and its selection, such as `#/runs/<id>` or
`#/jobs/<id>`, so refresh, browser back and forward and saved links return to the same
place. The sidebar opens the collection for Datasets, Jobs or Runs. Leaving a job with unsaved edits
asks first, whichever way you leave.

The Last Run status in each saved job row links directly to that Run. Run details show
the frozen job name, model and revision alongside a separate link to the current editable
draft. The current draft can have a different name or a later revision; opening it does
not change the Run or start work. Archived jobs are identified and link to the Jobs list,
where Archived jobs offers Restore. An unavailable job or a failed job lookup leaves the
Run and its downloads accessible.

To check this navigation manually, run the following from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Complete WAN acceptance 1, then follow its Last Run link in Jobs using Tab and Enter.
Its Checkpoints panel should list the final LoRA, and the Run should identify the frozen job revision. Follow the
current draft link, save a changed job name or step count, and use browser Back to return
to the Run. The frozen revision should stay the same and the current draft link should
show the new revision. Copy the Run address into another tab and verify the same details.
Archive the job, reopen the Run, and verify that it explains where to restore the archived
job while keeping its checkpoints downloadable. Repeat with WAN acceptance 2 to check that
similarly named jobs lead to their own Runs.

## Queueing

Add prepared runs to the pending queue while another operation owns the GPU. Move
entries earlier or cancel pending entries without changing active work. Ordering
and the exact enqueued revision survive service restarts. Preparation shares this
same GPU lease, so it cannot overlap training. Failures remain visible in history.

Waiting to train offers **Cancel queued training**: it removes that Run from the
queue, leaves prepared files on disk and does not start training. Waiting to
prepare offers **Cancel queued preparation**, separate from **Cancel preparation**
while caching is active.

Failed, cancelled and interrupted Runs offer **Prepare new Run from frozen inputs**.
This creates a separate Run with the original saved job revision and dataset inputs.
Only matching, hash-verified latent/text caches are eligible for reuse. Preparation
stops at Ready to start, even if the original Run used Prepare and start. Training
starts only when requested and runs again, followed by sampling if enabled; retry
does not restore optimizer state. Recorded leftover processes must be reconciled
first. To change inputs, open the editable job, save, review and prepare from Jobs.

To verify these actions, run from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Prepare both Wan jobs, start one and queue the other. Cancel queued training on the
waiting Run and verify it becomes Cancelled. Prepare a new Run from its frozen
inputs: the new Run should stop at Ready to start. In the fixture workspace, create
an empty `checkout/fail-train` file, start training and inspect the failed Run's retry
explanation. Retry should again prepare a new Run and wait for Start training. Remove
the marker before trying a successful training run. The CPU fixture checks control
flow, not GPU training quality.

## Stopping

Request graceful stop first. Windows console signalling can be unavailable for
some launch environments; inspect the reported result and use the separately
confirmed Force terminate action when necessary. Neither action promises a save
on stop. Only the observed process tree belonging to that Run is signalled.
A graceful stop sends one console break to the stage launcher's process group,
which its descendants share; if the launcher has already exited, nothing is left
to signal gracefully and remaining descendants need Force terminate.
When the launcher exits, its descendants, such as the Python child behind a
virtual-environment launcher, get up to 30 seconds to finish exiting. The Run then
becomes stopped and its active stage shows stopped. A descendant still alive after
that grace period makes the Run orphaned instead.

## Saved state

**Resume training from saved state** follows the Run summary and Actions on Run
Overview. **How resuming works** expands the explanation below. It also
works after restarting the application: open the stopped or interrupted Run,
choose **Inspect saved states**, enter **Additional segment steps**, then choose
**Restore as new segment** for a valid state and confirm. This immediately queues
training for those additional steps. The original Run stays in history.

Enable **Save optimizer/training state** in the Job before starting training and
set **Save every N steps** to a suitable checkpoint interval. Work since the last
saved state must run again. Without a valid saved state, optimizer restoration
is unavailable; prepare a new Run. The panel explains when state saving was
disabled or inspection finds no saved states.

Manual check from the repository root:

```powershell
$resumeWorkspace = ".cache/resume-check-$([guid]::NewGuid().ToString('N'))"
.\acceptance.cmd --workspace $resumeWorkspace
```

This isolated CPU fixture needs no models or GPU. In a fixture Job set 50 steps,
save every step and enable Save optimizer/training state. Prepare and start,
wait for an interval state in the outputs, then request graceful stop. Close and
relaunch the saved workspace with the command below, open the stopped Run and
restore a valid state with one additional step. Expect a separate completed Run
linked to the original, which remains Stopped. This checks orchestration, not GPU
training quality or seamless data-position continuation.

```powershell
$env:MUSUBI_GUI_STATE = (Get-Content "$resumeWorkspace/acceptance.json" -Raw | ConvertFrom-Json).state
try { .\launch.cmd --acceptance-fixture --port 8767 } finally { Remove-Item Env:MUSUBI_GUI_STATE }
```

Enable saved optimizer state in the Job before training. After completion or a
fully observed stop, Inspect saved states checks model/optimizer/scheduler/RNG
files and records hashes. Hashing large state folders takes seconds; the panel
shows that it is checking and accepts no repeat request until it finishes.
Missing, incomplete or changed files are rejected.

When the step count is a multiple of the checkpoint interval, the pinned upstream
saves the last step twice: once as the interval save `<name>-stepN` and again as
the final `<name>` checkpoint and `<name>-state`. After training succeeds, the
interval copy is removed only when it is proven identical. Its checkpoint must
have the same tensors and data. Its header metadata is ignored, since upstream
writes a different save date, epoch and finish time into each copy. Every state file must have the same
content hash. The final copies are always kept and are the ones Inspect saved
states, Restore and sampling use. The training log names each removed copy; a
copy that differs, or a check that fails, is kept. A state removal that fails
partway leaves a hidden `.<name>.duplicate` folder, which is never offered for
restoration; the log names it so it can be deleted.
Restore as new segment leaves the original Job/Run in place and records lineage and
the requested additional steps. The pinned upstream restores optimizer state
but resets its displayed step counter and data position; seamless continuation
is unsupported. Loading a LoRA checkpoint alone is not optimizer restoration.

## Windows Job Objects

On Windows each stage is assigned to a named Windows Job Object atomically during
process creation, before its first instruction. A separate acknowledged observer
holds a query-only job handle before stage creation and survives both controller
and initial-launcher exit. It remains until the controller is gone or closes the
job and every stage descendant has exited. Closing the application never enables
kill-on-close. The job name plus process creation time and executable identity
allow safe reconciliation after restart, including a crash before PID persistence.
Browser-independent logs can still be inspected.

## Recovery

If a process survives, the Run is orphaned and the GPU remains owned. Check for leftover processes refreshes observed state; Terminate recovered work requires confirmation
and targets only those owned processes. Uncertain identity/access or a crash during
launch blocks replacement GPU work. A known dead process/PID reuse does not count
as live ownership. Once all recorded work is gone, an orphaned Run with a requested stop
becomes stopped; after Terminate recovered work its stop is recorded as forced. A Run
already interrupted by a service stop stays interrupted. Otherwise it becomes interrupted,
with its running stage marked interrupted, and
can be retried using frozen inputs or a validated saved optimizer state. No retry
or replacement is launched automatically during uncertain recovery.
Older reserved launches without atomic job assignment remain readable but are
treated as uncertain when their ownership cannot be established safely.

## Deleting a Run

Delete run permanently removes a Run you no longer need and gives back its disk space.
The confirmation dialog names the Run, shows how much space is freed and says the delete
can't be undone; Cancel has focus and Escape cancels. It removes the Run record, its folder under the output root, its folder under
the cache root and its output index. There is no partial delete: to keep a LoRA, download
it first. Source datasets, other Runs, presets, model files, the Job and the shared
tokenizer under `<cache root>/shared` are left alone.

A delete is refused, with the reason, in these cases. Nothing is removed.

- The Run is active: queued, preparing, training, sampling or stopping, or orphaned or
  uncertain. Stop or cancel it first.
- The Run still records processes to reconcile. Choose Check for leftover processes first.
- A Run that is prepared or active still needs its files. A restored segment reads its
  source's saved state, frozen media and dataset files, and a retry reads the frozen media
  of the Run it came from. Cancel or delete that Run first.
- Its folders aren't exactly `<root>/runs/<token>` under the current Settings roots after
  resolving symlinks and junctions, or a folder is itself a link. A Run recorded under a
  root that is no longer configured stays visible until you restore that root in Settings;
  it can't be deleted or forgotten before then.

A finished Run doesn't block the delete of the Run it came from, and the confirmation
warns when there are any. The later Run keeps its own logs, checkpoints, samples and saved
states, and its `resume_of`, `retry_of` and `reused_from` fields keep the deleted ID. The
Runs screen doesn't show those IDs, apart from "verified cache reused", so nothing there
dangles. Its frozen inputs are gone, so Retry and Restore as new segment on it are refused
with the frozen-inputs message; use Prepare for a new Run instead.

A restored segment shares its source's cache folder. That folder stays until the last Run
using it is deleted, and the confirmation says so and leaves it out of the space freed.
Cache reuse copies files, so a Run that reused a cache keeps working when the Run it
copied from is deleted. Later preparation just stops finding that cache to reuse.

Links inside a Run folder are removed without being followed, and what they point to isn't
counted in the space freed.

The Run is marked `deleting` before anything is removed. A file that can't be removed,
such as one that is open in another program on Windows, doesn't stop the rest being
removed. The Run stays in the list and says what remains. The same happens if the backend
stops partway, including across a restart. A `deleting` Run can't be started, retried,
queued or restored from, its outputs aren't offered, and Prepare and cache reuse ignore
it. Delete again finishes the job. Run records stay version 1: `deleting` is one more
status with a `deletion` field, and no migration is needed. An older application lists such
a Run but doesn't know the status, so finish the delete in this version.

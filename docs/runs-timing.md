# Run timing, diagnostics and refresh

[Runs](runs.md) lists the other Run topics.

## Seconds per step

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

## Run elapsed

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

## Remaining-time estimates

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

For manual forecast verification use the isolated launch command under
[Run elapsed](#run-elapsed). Set a
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

## History refresh and network traffic

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

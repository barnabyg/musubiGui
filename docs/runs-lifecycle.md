# Preparing, training and stopping Runs

[Runs](runs.md) lists the other Run topics.

## Preparation

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
synthetic media scenarios are documented with the complete [release workflow](release.md).
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
maximum steps, as [remaining-time estimates](runs-timing.md#remaining-time-estimates)
describes. A restored segment continues the original Run's step count up to its
**Train until step** total, although upstream restarts its own counter; segments
restored by earlier versions count their own steps. Stage logs are collapsed below that, and open by
themselves when a Run has failed.

## Alternating Wan runs

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

## Save now, Sample now and Save and stop

While a Run is training, its Actions offer three requests that the trainer honours at
its next optimizer step:

- **Save now** saves that step's checkpoint and, when the Job saves training state,
  its state. Training continues.
- **Sample now** generates a preview at that step with the Run's frozen preview
  settings. It is unavailable, with the reason shown, when previews are off for the
  Job or the Run is alternating Wan, whose previews run as separate sampling stages.
- **Save and stop** saves that step's checkpoint and state, even when the Job doesn't
  save state otherwise, then ends the trainer cleanly before the next step. The Run
  ends as Stopped with a state from that step, which Inspect saved states accepts and
  Resume training restores.

One request waits at a time: the buttons stay disabled, and the Actions panel says
which request is waiting, until the step applies it. Then it says the step it ran at.
Extra saves and previews are named by their step like interval ones, so restored
segments name them by global step and alternating Wan by the expert's cumulative
step, and they appear with the Run's outputs. Checkpoint retention counts an extra
save like an interval save. At the final step the train-end save already saves, so
nothing extra is written.

The request is a file, `control-request.json`, in the Run folder, holding the action
and a request ID. Only training stages read it, at their post-optimizer hook; the
trainer reports the step in `control-handled.json` and removes the request. A
request still waiting when training reaches a sampling stage, or when the Run ends,
is removed and listed as not applied, so it never fires in a later stage or segment.
A Save and stop waiting at a sampling stage stops the Run there instead: training's
own end save has already been written. `POST /api/runs/{id}/control` with
`{"action": "save" | "sample" | "save_and_stop"}` makes a request; it returns 409 with
the reason when the Run isn't training, a request is already waiting or Sample now is
unavailable. The Run records the waiting request as `control_request`, each outcome
in `controls` with its step, and `stop_kind: "save"` after Save and stop.

To check them, open the acceptance workspace from [Queueing](#queueing), set a Wan
Job to 200 steps with a preview interval, then prepare, start and use Save now,
Sample now and Save and stop in turn. Each reports
the step it ran at; the checkpoint and preview appear in the outputs, and the stopped
Run's Inspect saved states lists a valid state at the stop step.

## Stopping

**Stop now** asks for confirmation that says how many steps trained since the last
save it will lose; Save and stop keeps them. It requests a graceful stop. Windows
console signalling can be unavailable for some launch environments; inspect the
reported result and use the separately confirmed Force terminate action when
necessary. Neither action saves on stop. Only the observed process tree belonging to
that Run is signalled.
A graceful stop sends one console break to the stage launcher's process group,
which its descendants share; if the launcher has already exited, nothing is left
to signal gracefully and remaining descendants need Force terminate.
When the launcher exits, its descendants, such as the Python child behind a
virtual-environment launcher, get up to 30 seconds to finish exiting. The Run then
becomes stopped and its active stage shows stopped. A descendant still alive after
that grace period makes the Run orphaned instead.

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
- Later segments of its training continue from it. Delete the whole training, or its later
  segments first.
- Its folders aren't exactly `<root>/runs/<token>` under the current Settings roots after
  resolving symlinks and junctions, or a folder is itself a link. A Run recorded under a
  root that is no longer configured stays visible until you restore that root in Settings;
  it can't be deleted or forgotten before then.

A finished retry doesn't block the delete of the Run it came from, and the confirmation
warns when there are any. The retry keeps its own logs, checkpoints, samples and saved
states, and its `retry_of` and `reused_from` fields keep the deleted ID. The Runs screen
doesn't show those IDs, apart from "verified cache reused", so nothing there dangles. Its
frozen inputs are gone, so Retry and Restore as new segment on it are refused with the
frozen-inputs message; use Prepare for a new Run instead. A segment whose source an earlier
version deleted on its own behaves the same way and starts its training in Runs.

### Deleting a training resumed in segments

Runs shows a training resumed in segments as one row (see
[chains of segments](runs-details.md#trainings-resumed-in-segments)). **Delete training**, on
that row or in the Actions of any of its segments, removes every segment after one
confirmation that lists them with their steps, revision and status. Each segment's folders
go as for Delete run, latest segment first, and the cache they share goes with the first
segment unless a Run outside the training still uses it. It is refused, with nothing removed,
while any segment is queued, preparing, training, sampling or stopping, or still records
processes, and when a Run outside the training is prepared or active and needs its files.
A delete that fails partway stops at that segment and leaves it and the earlier segments
listed; Delete training again finishes it.

Only the latest segment can be deleted on its own: **Delete segment** in the expanded row or
its Actions. An earlier segment is the source of a later one, so it goes with the training or
once the segments after it are gone. On a branched training each segment nothing continued
from, including a side branch's last segment, can go on its own.

`GET /api/runs/{id}/delete-preview?chain=true` returns the training's `chain` (its first
segment), `segments` and the combined `folders`, `bytes`, `shared_cache`, `dependents` and
`refusal`. `POST /api/runs/{id}/delete {"confirmed": true, "chain": true}` deletes it and
returns `deleted_ids`, latest first, and `freed_bytes`.

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

# Runs

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
The target hardware requirement is Windows 11, RTX 3090, 96 GB RAM and 400+ GB disk.

## Training

Choose Start training on a prepared Wan Run. Its process runs independently of the
browser. The job can be edited while the Run retains its original revision. Repeated
Start requests select the same Run. Logs display upstream progress without guessing
missing step counts; nonzero exits and missing checkpoints are explicit failures.
Checkpoint/sample locations stay associated with the originating Run.

## Queueing

Add prepared runs to the pending queue while another operation owns the GPU. Move
entries earlier or cancel pending entries without changing active work. Ordering
and the exact enqueued revision survive service restarts. Preparation shares this
same GPU lease, so it cannot overlap training. Failures remain visible in history.

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

If a process survives, the Run is orphaned and the GPU remains owned. Reconcile
processes refreshes observed state; Terminate recovered work requires confirmation
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
The confirmation names the Run, shows how much space is freed and says the delete can't
be undone. It removes the Run record, its folder under the output root, its folder under
the cache root and its output index. There is no partial delete: to keep a LoRA, download
it first. Source datasets, other Runs, presets, model files, the Job and the shared
tokenizer under `<cache root>/shared` are left alone.

A delete is refused, with the reason, in these cases. Nothing is removed.

- The Run is active: queued, preparing, training, sampling or stopping, or orphaned or
  uncertain. Stop or cancel it first.
- The Run still records processes to reconcile. Choose Reconcile processes first.
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

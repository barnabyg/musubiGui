# Preparation and immutable inputs

Preparation reserves snapshot creation through a separate state-scoped SQLite gate.
Duplicate requests from tabs or backend processes return the existing Run before
allocating another media/cache directory. Content hashes, effective settings, source
digest and stable runtime versions identify the snapshot; changing media bytes creates
a new identity even before an index re-scan. A request waiting over 30 seconds reports
that another snapshot is being created and can be retried safely.

Preparing an unchanged revision returns its existing Run, and the Jobs screen says
so and links to it. A Run that ended without completing (failed, cancelled,
stopped or interrupted) is not returned, unless it still records processes to
reconcile: Prepare creates a fresh Run of the same revision, for example after a
stop to change machine settings. Its caches are
reused through the stage fingerprints below, and the earlier Run keeps its logs,
outputs and saved states.

Before copying media, each new snapshot writes a versioned `snapshot.json` containing
its source manifest, settings and provenance. A directory with no published Run remains
an incomplete snapshot; caught failures mark it `incomplete` and retain the evidence.
The application preserves these directories for inspection rather than deleting their
contents automatically.

Save and review a Job, then choose Prepare. Runs persist the exact job revision,
effective included media, caption text, generated dataset TOML, model identities,
upstream revision and selected Python/package environment. Source media and captions
are copied into a separate per-run directory under the configured output root.
Source captions remain editable while caching runs against the frozen copies.

Preparation executes the pinned latent/text cache commands in the selected existing
training environment. Stage logs, exit codes, failures and cancellation survive
browser navigation. A zero exit without a cache for every included item is a failure.
Retries create a new run and preserve the previous inputs and diagnostics.

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

A transactional host-wide GPU lease serializes execution, including instances with
different application state directories. Pending work and history live in versioned
SQLite storage. Unknown storage versions are preserved and blocked.

Run `launch.cmd` to open the local application. The release acceptance command and
synthetic media scenarios are documented with the complete release workflow.
Synthetic subprocess tests exercise orchestration, never model-support evidence.
The target hardware requirement is Windows 11, RTX 3090, 96 GB RAM and 400+ GB disk.

Choose Start training on a prepared Wan Run. Its process runs independently of the
browser. The job can be edited while the Run retains its original revision. Repeated
Start requests select the same Run. Logs display upstream progress without guessing
missing step counts; nonzero exits and missing checkpoints are explicit failures.
Checkpoint/sample locations stay associated with the originating Run.

Add prepared runs to the pending queue while another operation owns the GPU. Move
entries earlier or cancel pending entries without changing active work. Ordering
and the exact enqueued revision survive service restarts. Preparation shares this
same GPU lease, so it cannot overlap training. Failures remain visible in history.

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

Enable saved optimizer state in the Job before training. After completion or a
fully observed stop, Inspect saved states checks model/optimizer/scheduler/RNG
files and records hashes. Hashing large state folders takes seconds; the panel
shows that it is checking and accepts no repeat request until it finishes.
Missing, incomplete or changed files are rejected.

When the step count is a multiple of the checkpoint interval, the pinned upstream
saves the last step twice: once as the interval save `<name>-stepN` and again as
the final `<name>` checkpoint and `<name>-state`. After training succeeds, the
interval copy is removed only when it is proven identical. Its checkpoint must
have the same tensors and data, ignoring the save date, epoch and finish time
that upstream writes into each header. Every state file must have the same
content hash. The final copies are always kept and are the ones Inspect saved
states, Restore and sampling use. The training log names each removed copy; a
copy that differs, or a check that fails, is kept. A state removal that fails
partway leaves a hidden `.<name>.duplicate` folder, which is never offered for
restoration; the log names it so it can be deleted.
Restore as new segment preserves the original Job/Run and records lineage and
the requested additional steps. The pinned upstream restores optimizer state
but resets its displayed step counter and data position; seamless continuation
is unsupported. Loading a LoRA checkpoint alone is not optimizer restoration.

On Windows each stage is assigned to a named Windows Job Object atomically during
process creation, before its first instruction. A separate acknowledged observer
holds a query-only job handle before stage creation and survives both controller
and initial-launcher exit. It remains until the controller is gone or closes the
job and every stage descendant has exited. Closing the application never enables
kill-on-close. The job name plus process creation time and executable identity
allow safe reconciliation after restart, including a crash before PID persistence.
Browser-independent logs can still be inspected.

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

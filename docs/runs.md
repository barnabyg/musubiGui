# Preparation and immutable inputs

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

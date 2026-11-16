# Saved state

[Runs](runs.md) lists the other Run topics.

## Restoring a saved state

**Resume training from saved state** on Run Overview continues training from a saved
state; **How resuming works** expands its explanation. It also
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

## Alternating Wan saved states

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

# Saved state

[Runs](runs.md) lists the other Run topics.

## Restoring a saved state

**Resume training from saved state** on Run Overview continues training from a saved
state; **How resuming works** expands its explanation. It also
works after restarting the application: open the stopped or interrupted Run,
choose **Inspect saved states**, enter **Train until step** (a total, such as 1000),
then choose **Restore as new segment** for a valid state and confirm. This immediately
queues training from the saved step to that total; the trainer runs the difference.
The total must be after the saved step: a state at or past it cannot be restored
until the total is raised, and the panel says which step to exceed. The field starts
at the Job's training steps. The original Run stays in history.

### Continuous step numbering

A restored segment continues the original Run's step numbering. Restoring a state
saved at step 300 with **Train until step** 500 and saves and previews every 100
steps produces checkpoints, states and previews named for steps 400 and 500, in the
new segment's folder. Intervals stay multiples of the overall step: resumed at 250,
an interval of 100 saves and previews at 300, 400 and 500. Progress shows the overall
step and the total, such as Step 350/500, and the timing record uses the same steps.
Preview checks, checkpoint retention and removal of a duplicated final save use these
overall steps. The final save keeps the unnumbered `<name>` and `<name>-state`, so in
that example the numbered save at 500 is removed once proven identical to it.

The pinned upstream still starts its own counter at zero and restarts the data order
from the beginning of the dataset on resume; the GUI adds the saved step to what the
trainer reports. Segments restored by earlier versions keep the numbering they were
trained with, counting their own steps.

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
inspect saved states. Set **Train until step** to the saved step of a valid state
(for example 3 for the state at optimizer step 3) and expect its Restore button to be
disabled with a note to choose a later step. Set it to two more (5) and restore. Expect
a separate completed Run linked to the original, which remains Stopped, showing
Step 5/5, whose output folder holds the checkpoint and state `-step00000004` (not
`-step00000001`) beside the final save. This checks orchestration, not GPU training quality or seamless
data-position continuation.

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
Restore as new segment leaves the original Job/Run in place and records lineage,
the saved step (`segment.step_offset`), the chosen total (`segment.target_steps`) and
the steps the trainer runs (`segment.additional_steps`). Segments restored by earlier
versions recorded only `additional_steps`, and remain readable. The pinned upstream
restores optimizer, scheduler and RNG state but restarts its data position; seamless
continuation is unsupported. Loading a LoRA checkpoint alone is not optimizer restoration.

## Alternating Wan saved states

**Inspect saved states** lists paired schedule positions, including interval saves
partway through a phase. Restoration freezes both available expert states as a new
segment and continues the schedule at that position. Expert checkpoints and states
keep their per-expert names. For newly prepared per-expert Runs, **Train until step**
is the step both experts reach, after catching up an interrupted unmatched pair; it
must be after the further-trained expert's step. Legacy Runs count total updates
across both experts, so the total is a schedule position. Progress shows the overall
position and the total, as for other restored segments. An early save before high-noise first trains records high at zero;
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

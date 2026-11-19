# Saved state

[Runs](runs.md) lists the other Run topics.

## Restoring a saved state

**Resume** in the Actions panel, and **Prepare and start** on the Job page, continue the
latest saved state; see [Resuming with an edited Job revision](#resuming-with-an-edited-job-revision).
**Resume from an earlier save** on Run Overview uses the Run's own revision. Set
**Train until step** to the desired total, then click **Resume** and confirm to continue
from the latest valid save. To pick a different save, click **Inspect saved states**
and choose **Restore as new segment** beside it. Both options work after restarting
the application and queue training from the saved step to the chosen total.
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
wait for an interval state in the outputs, then choose Stop now. Close and
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

## Resuming with an edited Job revision

Restoring a state reuses the Run's own commands, so its prompt and every other setting stay
as they were. To change some of them, edit the Job, then choose **Prepare and start** on the Job page
([Training from the Job page](jobs-current-training.md)), or open the stopped or completed Run
and choose **Resume** in its Actions panel. Resume inspects the saved states, picks the latest
valid one and compares the Job's latest revision with the revision the Run trained, field by
field. The result, shown under the button, lists the changed fields by tier and says whether
the state can take them. When it can, a confirmation, **Resume with revision N**, queues a new
segment from the saved step until the revision's **Training steps**, which is a total and must
be after the saved step. When it can't, nothing is asked: choose a save in **Resume from an
earlier save** to continue with the Run's own revision, or start fresh from the Job. The data order restarts, as for any restoration.
When the revision has warned changes, the confirmation lists them with the warning and
enables **Resume with revision N** only after **I understand these changes change what the
LoRA learns** is ticked.

| Tier                              | Fields                                                                                                                                                                                                                                                                                          |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Applies on resume                 | Name, training steps (after the saved step), final sample, sample prompt, sample inference steps, sample size and frames, generation memory policy, preview interval, save interval, kept checkpoints, blocks to swap, gradient checkpointing, inactive-DiT offload, and turning saved state on |
| Applies on resume, with a warning | Learning rate, timestep strategy, sigmoid scale, flow shift and gradient accumulation                                                                                                                                                                                                           |
| Resume with re-prepare            | Datasets with their repeats and resolutions, default repeats, and caption and media edits in the datasets themselves                                                                                                                                                                            |
| Needs a new Run                   | Model family, recipe, model components, LoRA rank and alpha, optimizer, runtime FP8 scaling, H2D-only block swapping, turning saved state off, expert interval, steps per expert, training size and frames, frame extraction, bucketing, batch size, cache policies, audio policy and seed      |

A field not listed, including one added in a later version, needs a new Run. A Run frozen
before a field existed compares it by the default it now reads as, so only real edits count.

### Warned changes

The warned fields apply on resume, but changing them mid-training changes what the LoRA
learns: the saved state was trained with the earlier values, so the result can differ from
a Run trained with either value throughout. Changing gradient accumulation also changes what
a step means, since each later step averages a different number of batches. Steps still
count optimizer updates, so step numbering, **Train until step**, save and preview intervals
and Save now work as before. The first update after the change can average fewer batches,
because the restored accumulation counter continues from the saved state. Time per step
changes too, so estimates from the source Run's timing are off until the segment reports
its own.

The segment's train arguments carry the new values. Loading the saved state restores the
optimizer's learning rate, so the app's trainer entry point writes the configured one back.
It records each optimizer parameter group's configured base learning rate when upstream builds
the scheduler, before the state loads, and at upstream's `on_train_start` hook, after the
state loads and before the first optimizer step, writes it into each group's `lr` and
`initial_lr` and the scheduler's `base_lrs`. Groups keep their ratios to the configured rate,
such as LoRA+'s scaled group, and the scheduler's warmup or decay factor at the saved step is
kept. The training log then shows
`Resume learning rate: applied 0.0002; the saved state had 0.0001.`, or
`Resume learning rate: 0.0001, unchanged from the saved state.` when nothing changes. A
Run that isn't resuming is untouched. Without it the pinned upstream keeps the saved learning
rate, for AdamW and AdamW8bit alike ([probe](reference/evidence/resume-learning-rate.json)).
Real GPU runs confirmed the override for H3 and alternating Wan, where each expert's first
resumed phase logs the applied rate ([H3](reference/h3.md#stop-edit-and-resume-17-november-2026),
[Wan](reference/wan.md#stop-edit-and-resume-17-november-2026)); the CPU fixture logs the same
line through the same code.

The segment's training and sampling commands are built from the new revision, plus `--resume`,
the saved step's numbering offset and `--max_train_steps` set to the total minus the saved
step. Its folder holds a new `previews.json` from the revision's preview settings, sealed
with its inputs, so previews from the saved step onwards use the new prompt; Sample now
follows the revision's preview setting. Training reads the source Run's frozen dataset
configuration, media snapshot and caches, and nothing is prepared or rebuilt, unless the
datasets changed (see [Resuming with a refreshed dataset](#resuming-with-a-refreshed-dataset)). The segment
records the new revision as its own `job`, so Compare, outputs and provenance describe what
it trained, and `segment.source_revision` and `segment.changes` record the revision it
continued from and what changed. Its checkpoints are named for its own revision. Like any
restoration, `resume_of` links it to the source Run, which is left unchanged.

Alternating Wan resumes both expert states of the chosen paired position and keeps the saved
schedule; the expert interval and steps per expert need a new Run. Paired previews and the
final sample use the new revision's prompt and sampling settings.

### Resuming with a refreshed dataset

A Run freezes copies of its media and captions, so a caption fixed or a clip added after it
started isn't in its snapshot. Resuming picks such edits up. The check compares the
datasets' current contents with the snapshot of the Run it resumes, item by item, and lists
them under **Resume with re-prepare**, such as `Dataset contents: 1 caption changed` or
`2 media added`, beside any re-prepare field. Edits to captions and media change no field of
the Job, so the Job's current revision resumes with them. Resolution, frames, frame
extraction and bucketing still need a new Run for now, though they only change caches.

Such a segment takes a new snapshot of the datasets into its own folder, with its own
`snapshot.json`, `dataset.toml` and cache folder, and records its own `manifest`,
`input_files` and `segment.dataset_changes`. It is queued to prepare and trains after it, from
the saved step with the same step numbering as any resumed segment. Each cache stage first
copies the source Run's verified cache files of every item whose cache inputs are unchanged,
renamed for its place in the new snapshot, then runs with `--skip_existing`, so only the other
items are cached. A stage with nothing left to cache doesn't run and shows as reused from the
source Run. An item's latent cache follows its media, the dataset's resolution and the Job's
cache settings; its text cache follows its caption. Changing one caption therefore caches
that item's text alone, and added media caches only the new items. An item renamed in the
dataset keeps the cache of identical content. Repeats change only `dataset.toml`, so nothing is
cached again. A source cache file that changed or went missing since is cached again rather
than copied. The source Run's files are never written.

Two cases cache more. H3 text caches read the item's media as well as its caption, so a
replaced H3 clip caches its text again too. Upstream's H3 latent cacher checks each cache
against the media's file path, which a new snapshot changes, so when any H3 latent must be
cached it caches them all; when none must, the source's latents are copied and the stage
doesn't run. Cache policies set to rebuild cache every item.

Review shows the work and disk use before resuming, under **Preparation before resuming** in
Job review's Resume compatibility, the Run's Resume result and the resume confirmation: which cache stages run,
the items each caches by name and how many reuse a cache, then the new media snapshot, the
copied caches and an estimate of the new caches, with the free space on each volume as Run
review checks it. Comparing the segment with its source in Compare lists the changed captions
and media item by item, with a `Dataset differences` summary.

When a dataset can't be read, for example because its folder moved, a resume without
re-prepare fields keeps the source's frozen snapshot and says so in the notes; one with them
is refused naming the problem. A model component, the training checkout or environment that
changed since the source was prepared refuses the refreshed segment, as preparing would.

Alternating Wan resumes with a refreshed dataset the same way: its cache stages come before
the restored schedule's phases.

`POST /api/runs/{id}/resume-check {job_revision?, target_steps?, state_id?}` returns the
comparison, changing nothing but the Run's recorded saved states: `compatible`,
`frozen_revision`, `revision`, `saved_step`, `target_steps`, `changes` (each with `field`,
`label`, `tier` of `editable`, `warned`, `reprepare` or `new_run`, `before` and `after`),
`blocking` (labels of fields needing a new Run), `errors`, `warnings` (why the warned changes
matter, empty without any), `notes`, `reprepare` (whether resuming snapshots the datasets
again), `dataset_changes` (`changed`, item names under `captions`, `added`, `removed` and
`replaced`, and `summary` lines; null when the datasets couldn't be read), `preparation` (null
unless `reprepare`: `items`, the cache `stages` that run, `latent_cache` and `text_cache` each
with `reuse` count and `rebuild` item names, `estimate` bytes of `media_snapshot`,
`copied_caches` and `new_caches`, and `volumes` as Run review reports them), `state_id` and
`state_name`. Comparing the current datasets hashes their media, as preparing does. It
defaults to the Job's latest revision, the latest valid state and the revision's steps.
`POST /api/runs/{id}/resume {job_revision, target_steps?, state_id?}` queues the segment:
`pending_training` when it reads the source's snapshot, or `pending_preparation` when it
re-prepares, after which it trains on its own.
A revision that changes a field needing a new Run, or a total at or before the saved step,
is refused with 422 and a message naming the fields. Like every action, both refuse requests
from another site. Restore as new segment (`restore-state`) is unchanged.

Manual check from the repository root:

```powershell
$resumeWorkspace = ".cache/resume-revision-$([guid]::NewGuid().ToString('N'))"
.\acceptance.cmd --workspace $resumeWorkspace
```

In WAN acceptance 1, set 200 steps, save every 200 steps, enable Save optimizer/training
state, turn off the final sample, then prepare and start. While it trains, choose **Save and
stop**. In the Job, set LoRA rank 16 and save. On the stopped Run choose **Resume**: expect
LoRA rank under Needs a new Run and no confirmation. Set the
rank back to 8, change the sample prompt, set previews every 70 steps and 210 training steps,
and save. Review configuration should show Resume compatibility naming that Run, with the
prompt, steps and preview interval under Applies on resume. Set the learning rate to 0.0002
and save: review lists Learning rate: 0.0001 → 0.0002 under Applies on resume, but changes
what the LoRA learns, with the warning. On the Run, choose Resume again: the confirmation
keeps its resume button disabled until the acknowledgement is ticked. Expect a new completed
segment at Step 210/210 whose Samples show the step 70, 140 and 210 previews with the new
prompt and whose training log shows `Resume learning rate: applied 0.0002`, while the
original Run stays Stopped with its revision.

To check a refreshed dataset, stop WAN acceptance 1 with **Save and stop** as above, then in
Datasets open **WAN acceptance**, select motion.mp4 and change its caption. Review
configuration on the Job: Resume compatibility lists `Dataset contents: 1 caption changed`
under Resume with re-prepare, and Preparation before resuming says text caching runs for
motion.mp4 alone while latents reuse the Run's caches, with the extra disk use. On the Run,
choose Resume and confirm. Expect the new segment to prepare, show its latent cache
as reused and its text cache completed, then train to the total. Compare it with the source
Run: the caption's old and new text appear, and the source keeps its caption.

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
of the inactive expert's source LoRA, so both files are available even if only one
expert trains in the new segment.

Stop now does not force a new save. Save and stop saves the active expert's
checkpoint and state at the step it stops, with the usual per-expert name, and
Inspect saved states pairs it with the other expert's latest state. Choose a complete saved
schedule position and expect any unsaved updates after that position to be lost. Restoration validates the
state files and their counters; missing or changed state blocks it. Retry after paired
training has started directs you to inspect/restore paired state rather than silently
restarting either expert. Preparing the Job again explicitly starts both experts over.
Restoration retains source dependencies; active/queued segments prevent deleting their
source Run. Musubi restores optimizer, scheduler and RNG but starts a fresh dataset
pass at each phase, including restored phases; exact batch position is not preserved.

# Job editor and revisions

How to edit, save, review and manage a job. [Jobs](jobs.md) lists the other job topics.

## Compact job setup

Jobs opens on **Job & dataset**. Separate tabs contain Training, Samples, Dataset preparation,
Memory, Checkpoints, Presets and Review. Training holds the required choices, step count,
optimizer, timestep strategy and LoRA settings. Use Left/Right arrows to switch tabs,
or Home/End to select the first/last tab. This follows the separate-panel behaviour of Runs tabs.
Samples holds final-sample enablement, preview cadence, prompt, inference steps,
resolution, duration and the shared training/sample seed.
Previews and the final sample can be enabled independently and share these settings.
Dataset preparation and checkpoint controls no longer contain sample-only controls.
New Wan jobs use alternating high/low training. Low-noise-only training and its
two-step smoke preset are no longer offered. Saved single-expert Wan presets are
also omitted from the preset selector.
Required choices names anything still missing, including the sample prompt when sampling
is enabled. Turn sampling off explicitly if no final sample is needed. The compact view
shows the draft's current size, frame count and rank; new drafts start from the measured
target-PC defaults. Review still checks media, model files and every saved setting.

Tabs above the form show one group of fields at a time. Each field belongs to exactly one tab,
and switching tabs preserves unsaved edits. Scrolling does not change the selected tab. Simple rules
such as multiples, frame formulas and ranges are flagged beside their field while typing, and
never block saving a draft. Review stays the authority: each of its errors about a field is
a link that focuses that field.

Open the relevant tab to change its fields. Reopening a saved job or importing a configuration
preserves every value. Review configuration opens the Review tab when its result arrives.
Review error links select the owning tab and open any collapsed details before focusing the
field. Generated configuration remains under Advanced in Configuration review.

To verify manually, run this from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Create a Wan job, choose a dataset, and enter a sample prompt or turn off the final sample.
For the fixture's short WAN acceptance clip, open Presets and apply its Smoke
test preset before review; normal 33-frame-or-longer clips use the new-job recipe directly.
Repeat with H3 acceptance, which supports the default 124 frames. Review should pass without
changing memory or optimizer settings. In Training, change LoRA rank, switch to Memory
and back: the edit should remain, with only the selected tab’s fields visible. Save, reload,
and return to Training: the saved rank should remain. Set an invalid width in Dataset
preparation, review, and follow the Geometry error: Dataset preparation should open with the
width focused. Check arrow-key navigation and a window about 700 pixels wide; the form should
stay within the window.

## Configuration review

Review configuration scrolls to its result, which opens with pass or fail, the error
count, the included item count and whether the disk has room. Errors and warnings
follow. The disk breakdown is collapsed, and Advanced holds the resolved component/output
paths, model provenance, native dataset TOML and each upstream argument array. Errors
block preparation while the draft remains editable. Review configuration saves unsaved
edits as a new revision first; if the save fails, the review stops and the edits stay on
screen. Prepare run and Prepare and start use the revision that passed review. If another
tab saves a newer revision, preparation reports the old and new revision numbers and stops.
Choose Review configuration to load the saved changes and review them before preparing.
Component overrides must stay inside the configured
model roots. The original Wan recipe trains the low-noise expert. A separate
[native-weight CLI acceptance scenario](reference/wan.md#native-weight-qualification)
also passed high-noise-only training; that recipe is not exposed by the GUI.
The [alternating recipe](jobs-training.md#alternating-wan-highlow-loras) trains both
experts sequentially. Simultaneous combined
training remains unqualified. Runtime FP8 scaling requires native FP16 sources,
not saved scaled-FP8 weights. Inactive-DiT offload is not supported by this recipe.

### Resume compatibility

When the Job has resumable training, Configuration review adds **Resume compatibility**.
It compares the reviewed revision with the Run that **Prepare and start** would resume: in the
Job's current training, once it has ended, the most recently created stopped, completed,
failed, cancelled or interrupted segment with a saved state (see
[Training from the Job page](jobs-current-training.md)). It links that Run, gives its revision and saved step, and says
whether its saved state can resume with this revision. Changed fields are listed by tier
(Applies on resume, Applies on resume with a warning, Resume with re-prepare, Needs a new
Run), each with its old and new value, followed by the note that the data order restarts.
Datasets, repeats and per-dataset resolutions resume with re-prepare, and so do caption and
media edits in the datasets since that Run froze them, listed as `Dataset contents` lines;
**Preparation before resuming** then gives the caching and disk use that resuming adds (see
[Resuming with a refreshed dataset](runs-saved-state.md#resuming-with-a-refreshed-dataset)). Learning rate, timestep
strategy, sigmoid scale, flow shift and gradient accumulation apply with a warning that
changing them mid-training changes what the LoRA learns, and for gradient accumulation what
a step means; resuming asks to acknowledge it (see
[Warned changes](runs-saved-state.md#warned-changes)). Training steps at or
before the saved step are reported too. The saved step comes from the Run's last **Inspect
saved states**; before that, review reports state folders without a step and skips that
check. Review never hashes states; it hashes the datasets' media to compare their contents,
as preparing does. Prepare and start resumes that Run when the revision can take it, and
otherwise offers a fresh training; Prepare run always prepares a new Run. Run details offers
the same check as [Resume](runs-saved-state.md#resuming-with-an-edited-job-revision), which
also lists the tiers.

## Drafts and revisions

Launch with `launch.cmd`, register a dataset, and open Jobs. Jobs are listed in a table
of name, model, dataset, revision, last Run and last edited time, most recently edited
first; choose a heading to sort by it. The dataset column shows the first dataset and how
many more follow, such as `Susan images +1`. New Wan job creates an incomplete saveable draft
with a name no other job is using, ready to retype. Add its datasets and edit the grouped preparation,
LoRA, memory, checkpoint and sample controls. Save draft, Review configuration, Prepare
run and the status stay visible in a bar as you scroll. Duplicate job, Change model,
Archive job, Delete job and Import / export configuration are in the bar's ⋯ menu.
Save draft creates a new immutable
revision; reopening the application retains every supported value. An outdated tab
cannot overwrite a newer revision.
Revision allocation is serialized across backend processes sharing the same state
directory, including saved drafts, confirmed model conversions and preset application.
Interrupted writes retain their temporary files; only published revisions appear
in the library. Unknown storage remains blocked and preserved for repair.

## Revision and export versions

A version 2 revision or export lists its datasets: `datasets` holds a `dataset_id` and
`repeats` for each one. Blank repeats inherit the job's `repeats`, which stays the
`[general]` default, so presets are unaffected. Version 1 had a single `dataset_id`.

New revisions and exports are written as version 2. Version 1 files stay readable and are
never rewritten: a version 1 revision opens as a job with one dataset, and saving it creates
a version 2 revision beside it. A version 1 export imports the same way, and API requests may
still send `dataset_id`. A file that mixes the two shapes is rejected, like an unknown version
or field.

An older application version cannot read version 2. It reports the revision as unknown
storage and blocks that job, so it only affects jobs saved after upgrading. Runs prepared
before this change keep working, because each uses its own frozen records. Preparing a revision
that already has a Run from before the upgrade creates a new Run rather than returning that
one, and its unchanged caches are reused.

Dataset exclusions belong to the dataset, not the job, so they apply to every job using it.

## Model conversion

Model conversion first previews every changed model/task-specific field. Cancel
leaves the previous draft intact; Accept creates a revision while retaining the old
revision for comparison. Incompatible components and memory/recipe/frame defaults
reset, and shared controls remain. Size, rank and alpha still at the source model's
new-draft default move to the target model's, and values you chose are kept.

## Import and export

Import / export configuration downloads strict `musubi-gui-job` version 2 JSON, and
accepts [versions 1 and 2](#revision-and-export-versions). Reimport creates an independent job. Unknown versions, formats and fields are
rejected; missing optional controls in earlier exports receive documented family
defaults. Recognised credential strings are redacted on export/import and in
presets. Review local filesystem paths before sharing a configuration. No model,
dataset or training-state payload is embedded in the export.

## Archiving a job

Archive job hides a job you no longer need, even while it has Runs, because each Run's
frozen snapshot records the job's identity and revision. Nothing is
removed from disk, and revision files are not rewritten: the archive is a
separate marker file in `archived-jobs` in the state directory. An older application
version ignores the marker and lists the job again.

An archived job leaves the job list and can't be edited, converted, used as a preset
source or prepared. Presets already saved from it stay, since they are independent
copies. Its Runs, outputs and saved states stay browsable from Runs. Those Runs also keep
working, because each uses its own frozen snapshot: you can still start a prepared Run,
retry a failed one or restore a saved state, even though that makes the archived job's
Run active again. Archived jobs lists these jobs with a Restore button, which brings a
job back unchanged.

A job can't be archived while it has an active Run: queued, preparing, training,
sampling or stopping, or still holding processes to reconcile. The refusal says why, and
the job stays in the list. Stop or cancel the Run first.

## Deleting a job

Delete job permanently removes a job and all its revisions, and works for listed and
archived jobs alike. It is refused while any Run of that job remains, whatever the Run's
state, including a Run with an unfinished delete. The refusal points to
[Delete run](runs-lifecycle.md#deleting-a-run) for each Run, or Archive job to hide the job instead.
Deleting a job and its Runs in one action was rejected, because it would remove far more
than the action suggests. Datasets, presets saved from the job and other jobs are left
alone. An interrupted delete leaves the job listed at its latest revision, to delete again.
Unknown job storage or an unknown Run database version blocks the delete.

## Checks

Focused checks: `npm.cmd run typecheck` and
`.venv\Scripts\python.exe -m pytest tests/python/test_jobs_api.py -q`. Browser checks
cover the editor through the API and persistent storage. For manual review, save an
incomplete draft, restart, change frame count to 18 and enable inactive-DiT offload.
Review must explain both errors and disable Prepare run. Correct the fields, compare
the reviewed TOML and arguments with the controls, and verify reopening retains them.
Real preparation/training requires the compatible model files and environment described
in [reference workflows](reference/README.md); fake acceptance fixtures do not prove
model support.

To check two-tab review in an isolated CPU fixture, run this from the repository root in PowerShell:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Open WAN acceptance 1 in two tabs. Review in A, rename and save in B, then choose
Prepare and start in A. Expect the revision-change message and an empty Run history.
In A, choose Review configuration, confirm the new name, then choose Prepare run.
The Run in history shows the new name and the revision that passed review.

Browser checks: `npm.cmd run test:browser -- tests/browser/job-` runs every
`tests/browser/job-*.spec.ts`. The Job editor specs below share
`tests/browser/jobHarness.ts`. `job-drafts.spec.ts` covers restart persistence and
generated configuration for both models, and frame extraction with cached-window
counts for a resampled 30 fps clip. `job-review-errors.spec.ts` covers invalid draft
feedback and Review errors that lead to their field. `job-transfer.spec.ts` covers
duplication, presets, cancelled/accepted conversion and configuration round-trip.
`job-actions.spec.ts` covers the action bar, the job collection and confirmation
dialogs, `job-editor-layout.spec.ts` the form layout and tabs, and
`job-library-load.spec.ts` library and preset load failures. These UI scenarios
intentionally do not launch GPU work; the separate release-acceptance fixtures
exercise execution and real hardware evidence remains separate.

To check collection navigation manually, run `./launch.cmd` from the repository root
(`C:/docs/git/musubiGui`). This prepares and launches the application. In Jobs,
create a draft and confirm the editor shows no saved-job list or creation buttons.
Edit its name, choose Back to jobs, and check that Keep editing preserves the edit.
Save the draft and return to Jobs; it should appear in the collection. Open it again
and use browser Back/Forward and reload to check navigation. In Datasets, open an
existing dataset and return to its library. With a prepared Run available, open it
from Runs and check that history and the pending queue appear only after returning
to Runs. Dataset and Run checks require an existing dataset and a reviewed job with
configured model paths respectively.

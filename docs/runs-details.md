# Run details, folders and links

[Runs](runs.md) lists the other Run topics.

## Run details

**Overview** shows the Run state, training progress, timing and actions. The short Run ID
beside the Job name matches the first eight characters of the ID in the Run URL.
Open **Stage logs and diagnostics** to read logs or select the output folder path.
The Run folder contains its LoRAs directly and keeps saved training states under
`state/`. See [Run outputs](outputs.md) for the full layout.

**Samples** lists generated media and periodic previews. Use the tabs or arrow keys to switch
views. The sample selection and playback stay in place when you switch views or new previews arrive.
Use **Refresh outputs** on Samples to rescan the available files.

## Trainings resumed in segments

Each restored or resumed segment is stored as its own Run, linked to the Run it continued by
`resume_of`. A Run and every segment resumed from it, directly or through other segments,
form one training, which `musubi_gui/run_chains.py` defines for every screen. Stored Runs are
not changed, so Runs saved before this version group the same way.

- **Runs** shows the training as one row named after its latest segment, with that segment's
  status and the segment count. The row opens the latest segment; **Show N segments** lists
  each one with its steps, revision and status, linking to its own details.
- **Run details** of any segment add a **Segments** panel: the training's step against the
  latest segment's **Train until step** total, and each segment's step range, status and Job
  revision. Live progress and the remaining-time estimate of the training segment already
  count global steps, so they cover what is left of the whole training.
- **Samples** lists every segment's previews together in global step order,
  each marked with its segment. Checkpoints and saved states stay per segment, since you inspect and resume
  them from the segment that saved them.

A step range reads `steps 120–250`: from the saved step the segment resumed to the step the
next segment resumed from, or to its total once it completed. A segment still running, or
ended without a later one, shows its total as `(target)`. Segments restored before continuous
step numbering count from their saved step; their own files keep the names they were given.

When one saved position was resumed twice, for example after a segment failed, the training
follows the most recently created segment: its line runs from the first segment to that one,
and the Runs row, merged outputs and retention follow it. The other segments stay in the
training as side branches, listed in creation order and marked as such, and their own Samples
show the line up to them. Creation time decides, with the Run ID breaking a tie. A retried
segment continues from the same source as the segment it retried. If the Run a training
started from was deleted by an earlier version, the training starts at its first remaining
segment and says so.

Deleting is covered in [Deleting a training](runs-lifecycle.md#deleting-a-training-resumed-in-segments)
and retention in [checkpoint retention](jobs-checkpoints.md#across-a-training-resumed-in-segments).

`GET /api/runs/summaries` gives each Run's `chain`: its `id` (the first segment), `size`,
`position`, `latest`, `on_line`, `source`, `missing_source`, `later_segments` and `steps`
(`start`, `target`, `end`, `legacy`).

To check this by hand, open the acceptance workspace from
[Queueing](runs-lifecycle.md#queueing). In WAN acceptance 1 set 200 steps, previews every 2
steps and turn off the final sample, then prepare and start. After a preview, choose **Save and
stop**. In **Resume**, set **Train until step** to 200, choose **Review resume** and confirm.
After a preview, save and stop again and resume with a total four steps later. Runs should show one
row with 3 segments and Completed. **Show 3 segments** lists their step ranges. The row opens
the latest segment, whose Segments panel lists all three and whose Samples list every preview
in step order. At 375 px nothing scrolls sideways.

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
restoration. Saved-state folders use the matching stem with `-state`. Existing Runs keep their frozen names,
including older `phase00000000` identifiers that record the phase's schedule start;
they remain available in their recorded folder. Newly prepared Runs and restored segments use
completed expert steps.

To check Wan checkpoint naming, run this command from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/wan-checkpoint-names-$([guid]::NewGuid().ToString('N'))"
```

This isolated CPU fixture needs Windows, Node.js/npm, uv and Edge; no GPU or models.
Open **WAN acceptance 1**, set Training steps to 4, steps per expert before switching
to 2, and Save every N steps to 1. Save, review, then **Prepare and start**. In the
Run's output folder, expect each expert's checkpoints at steps 1, 2, 3 and 4 and the
matching folders under `state/`. In **Resume**, open **Choose an earlier save** and select the pair at step 2.
Set **Train until step** to 4, review and confirm. The new segment's final files should both end in `-step00000004`.
GPU training quality is outside this naming check.

To check folder naming, from the repository root run
`.\acceptance.cmd --workspace ".cache/folder-check-$([guid]::NewGuid().ToString('N'))"`.
Prepare a fixture Job and open **Stage logs and diagnostics** to inspect the output path. It should contain
the Job name and timestamp, without a random suffix. The fixture needs no models
or GPU. Preparation creates synthetic training inputs. Start training, copy the output folder path
and inspect the checkpoints at the top level and saved states under `state/`. Both should
use the frozen Job name and revision, with no random job ID. Rename the Job and confirm
the original Run still offers the same folder and its saved states.

## Links and browser history

The address bar names the screen and its selection, such as `#/runs/<id>` or
`#/jobs/<id>`, so refresh, browser back and forward and saved links return to the same
place. The sidebar opens the collection for Datasets, Jobs or Runs. Leaving a job with unsaved edits
asks first, whichever way you leave.

The Last Run status in each saved Job row links directly to that Run. The Run shows its
recorded Job name even after the Job is renamed or archived.

To check this navigation manually, run the following from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Complete **WAN acceptance 1**, then follow its Last Run link in Jobs using Tab and Enter.
Check that the short Run ID matches the beginning of the ID in the address bar.
Rename or archive the Job from the Jobs screen, then reopen the Run from its saved URL.
Expect the original Job name and output path under **Stage logs and diagnostics**.
Repeat the Last Run link check with WAN acceptance 2 to verify that it opens a different Run.

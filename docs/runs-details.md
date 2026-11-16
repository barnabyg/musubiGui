# Run details, folders and links

[Runs](runs.md) lists the other Run topics.

## Run details

Run details has two tabs: **Samples**, for generated media and periodic previews, and
**Overview**, for everything else: state, progress, actions, saved state, caching,
checkpoints, comparison with another Run, inputs and logs.
`tests/browser/run-details-layout.spec.ts` pins the panels on each tab. Use the tabs or
arrow keys to switch views. Switching views keeps the selected sample and its player
mounted, so returning preserves playback. New previews do not replace a selected sample.
Take a LoRA from the Checkpoints panel or straight from the Run's output folder.

## Output folders and names

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
restoration. Saved-state folders use the matching stem with `-state`. Download names
retain the expert and step (without zero padding). Existing Runs keep their frozen names,
including older `phase00000000` identifiers that record the phase's schedule start;
they remain downloadable and restorable. Newly prepared Runs and restored segments use
completed expert steps.

To check Wan checkpoint naming, run this command from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/wan-checkpoint-names-$([guid]::NewGuid().ToString('N'))"
```

This isolated CPU fixture needs Windows, Node.js/npm, uv and Edge; no GPU or models.
Open **WAN acceptance 1**, set Training steps to 4, steps per expert before switching
to 2, and Save every N steps to 1. Save, review, then **Prepare and start**. In the
Run's output folder, expect each expert's checkpoints and state folders at steps 1,
2, 3 and 4; the paired downloads should end in `-low-step4.safetensors` and
`-high-step4.safetensors`. Inspect saved states and restore the pair at step 2 with
**Train until step** 4: the new segment's final files should both end in `-step00000004`.
GPU training quality is outside this naming check.

To check folder naming, from the repository root run
`.\acceptance.cmd --workspace ".cache/folder-check-$([guid]::NewGuid().ToString('N'))"`.
Prepare a fixture Job and inspect the Output path on its Run. It should contain
the Job name and timestamp, without a random suffix. The fixture needs no models
or GPU; preparation creates synthetic training inputs. Start training, download the final
LoRA from Checkpoints and inspect the Run's `checkpoints` folder. Checkpoint files and state folders should
use the frozen Job name and revision, with no random job ID. Rename the Job and confirm
the original Run still downloads and offers its saved states.

## Links and browser history

The address bar names the screen and its selection, such as `#/runs/<id>` or
`#/jobs/<id>`, so refresh, browser back and forward and saved links return to the same
place. The sidebar opens the collection for Datasets, Jobs or Runs. Leaving a job with unsaved edits
asks first, whichever way you leave.

The Last Run status in each saved job row links directly to that Run. Run details show
the frozen job name, model and revision alongside a separate link to the current editable
draft. The current draft can have a different name or a later revision; opening it does
not change the Run or start work. Archived jobs are identified and link to the Jobs list,
where Archived jobs offers Restore. An unavailable job or a failed job lookup leaves the
Run and its downloads accessible.

To check this navigation manually, run the following from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Complete WAN acceptance 1, then follow its Last Run link in Jobs using Tab and Enter.
Its Checkpoints panel should list the final LoRA, and the Run should identify the frozen job revision. Follow the
current draft link, save a changed job name or step count, and use browser Back to return
to the Run. The frozen revision should stay the same and the current draft link should
show the new revision. Copy the Run address into another tab and verify the same details.
Archive the job, reopen the Run, and verify that it explains where to restore the archived
job while keeping its checkpoints downloadable. Repeat with WAN acceptance 2 to check that
similarly named jobs lead to their own Runs.

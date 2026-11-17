# Run outputs and revision comparison

Choose **Copy output folder** in Run Overview and paste the path into File Explorer.
Open its `checkpoints` subfolder to select and copy LoRAs together. New checkpoints
and saved-state folders use the frozen job name and revision, for example
`My-Job-r2.safetensors` and `My-Job-r2-state`. Alternating Wan saves retain each
expert's step or phase in the filename. Every Run has a separate folder.
Existing Runs keep their recorded paths and filenames.

Choose **View sample** to open this Run’s latest available image, video or audio preview
in one action. Use the Samples tab to select earlier media, inspect metadata or
download a sample. For resumed training the Samples tab includes other segments
while the folder action always identifies the selected Run. Completed Runs show
actual elapsed time. A Run with sampling disabled or no playable sample shows
its state. Output warnings, including incomplete Wan pairs, appear in Overview.
Saved optimizer state files remain available in the resume workflow.

The inventory survives restart and retains previously observed missing files.
Missing/moved, unreadable, empty/truncated and blocked outputs have separate status
messages; browsing never deletes or repairs a source file. The inventory reloads by itself
whenever the Run or one of its stages changes state, keeping the selected output and any
playing preview. Refresh outputs rescans the selected Run. An available file means it can be read/decoded or its checkpoint
header/payload bounds can be inspected; it does not establish training quality,
complete optimizer state or resumability. H3 video samples without a decodable audio
stream are explicitly incomplete joint AV output.

Every file request resolves the path again inside the originating Run directory and
both its recorded output root and the current Settings output root. Junction/symlink
escapes, arbitrary paths and identities belonging to another Run are rejected.
Changing the configured root blocks access to old locations until the original
root is restored. Moving an output does not silently reassign its provenance.

[Delete run](runs-lifecycle.md#deleting-a-run) removes a Run's outputs and its index entry with it,
so copy the files you want to keep first. While a delete is unfinished, the Run offers no
outputs. Comparison lists neither deleted Runs nor Runs being deleted; comparing with a Run
deleted from another tab since the list loaded reports that it was not found.

Under Compare with another Run, Compare inputs displays the changed Job
controls/revisions, Settings, dataset/caption manifests, model identities and
upstream/environment provenance of the two Runs' frozen inputs.
It compares recorded facts and offers no quality ranking. Comparing changes no Job or
Run.

The output API for a training resumed in segments lists its checkpoints and samples in
global step order, marked with their segment; each file is still served by the Run that
made it. The final LoRA is the latest segment's. `GET
/api/runs/{id}/outputs?chain=true` returns this list for the training `{id}` belongs to,
adding `segment` (`run_id`, `position`, `revision`, `status`) and `step` to each output and
`segments` and `latest` to the result; a Run never resumed returns its own list unchanged,
plus `segments` and `latest`. Without `chain`, a segment lists only its own files.

The picker also offers each training resumed in segments as **Training: name · r… · N
segments**. Such a side compares the inputs of the training's latest segment, the revision it
trains now, and its sample selector lists the samples of every segment. When the current Run
is a segment, **Compare from** chooses between its whole training (the default) and this
segment only. Any single segment can still be picked as the other side.

Comparison also shows an independent sample selector for each Run alongside those
differences. Both sides identify the job, frozen revision and full Run ID, and show
the sample prompt and seed recorded in the Run settings when present. These settings
are provenance, not a claim about the quality of a particular file. Videos and audio
have playback controls; missing samples keep their availability message, and a Run
that has no generated samples remains comparable. On narrow screens the two sample panels stack.

One command creates **two synthetic Runs per model** and launches their real media
output browser, without model weights or GPU work:

```powershell
.venv\Scripts\python.exe -m musubi_gui.output_fixture .cache\output-demo --launch
```

Use a new destination; existing directories are preserved. Open the local URL on
port 8768, choose Runs, and inspect a Wan and H3 fixture. Play their videos, the H3
audio sample and the still image. Copy the output folder path into File Explorer. Compare revision one with
revision two and inspect changed rank, caption, model identity and upstream version.
Select and play a sample on each side without leaving comparison, then select H3's
audio sample. Check both final LoRA filenames in their respective output folders.
Repeat at 390 px width. Comparing a Run prepared with sampling disabled should show
no generated samples on that side while keeping its recorded input differences usable.
Remove one generated fixture file, refresh, restart the service and confirm its
missing status remains visible. The fixture manifest lists exact paths and Run IDs.
All fixture media is original CC0 synthetic content and its tiny checkpoint is
labelled synthetic, not trained.

To check the paired checkpoint workflow manually, run this command from the repository
root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/paired-outputs-$([guid]::NewGuid())" --wan-paired
```

Review and start a seeded Wan job. At 1280 × 720, open its completed Run. Find the
job name, completion state, elapsed time, **Copy output folder** and **View sample**
in the initial view. Paste the folder path into File Explorer, open its checkpoints folder, and copy
the low/high checkpoint files together. Return to Overview and choose **View sample**
to see the playable sample immediately.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/python/test_outputs_api.py -q
npm.cmd run test:browser -- tests/browser/outputs.spec.ts
npm.cmd run test:browser -- tests/browser/sample-comparison.spec.ts
```

The API tests cover persistent/missing inventory, checkpoint bounds, image/audio/video
delivery and byte ranges, H3 missing audio, comparison, current-root changes, cross-run
identities and a Windows output-folder junction escape. The Edge scenario covers both
models, actual muted video/audio playback, images, folder selection, comparison, file removal
and backend restart. Audible quality, real-model sample quality and new capacity
settings require separate human/hardware acceptance.

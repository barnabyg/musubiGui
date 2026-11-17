# Run outputs and revision comparison

Choose a persistent Run. Output warnings and sample errors appear in its Run summary.
The Checkpoints panel on Overview lists, collapsed, the LoRA checkpoints with step and
size, and the Samples tab lists the samples; weights inside a saved state's folder are
listed under Optimizer state files, not as LoRAs. Select a file to inspect its originating
Run, metadata and availability. Checkpoint and sample selections are kept separately.
Image, video and audio samples play in the browser; Open output in browser and
Download output use only the recorded file identity. Checkpoint downloads remain
binary attachments, named after the job as `<job name>-r<revision>[-step<N>].safetensors`
with unsafe filename characters replaced. New checkpoint files and saved-state folders on disk
also use the Job name and frozen revision, for example `My-Job-r2.safetensors` and
`My-Job-r2-state`. Step checkpoints add `-step<N>`; paired checkpoints retain their expert
and phase suffixes. Each Run's separate output folder prevents same-name Jobs from
overwriting each other. Existing Runs keep their recorded names and paths, including
job-ID names. Legacy `.pt`/`.pth`/`.ckpt` files are never unpickled.

Alternating Wan Runs list each expert's checkpoints under Checkpoints. Download names
include the expert and phase (or `source` for a checkpoint copied into a restored
segment), so the two files do not share a download name. Earlier phase and interval
checkpoints remain listed. An incomplete pair is reported as a warning in the Run
summary once the Run has completed, failed, stopped, been cancelled or been
interrupted. Saved-state weights remain separate from
the downloadable LoRAs. Paired sample metadata records both exact checkpoint paths,
SHA-256 digests and step counts; sampling refuses missing or changed trained checkpoints.

The inventory survives restart and retains previously observed missing files.
Missing/moved, unreadable, empty/truncated and blocked outputs have separate status
messages; browsing never deletes or repairs a source file. The inventory reloads by itself
whenever the Run or one of its stages changes state, keeping the selected output and any
playing preview. Refresh outputs picks up a checkpoint saved partway through a stage. An available file means it can be read/decoded or its checkpoint
header/payload bounds can be inspected; it does not establish training quality,
complete optimizer state or resumability. H3 video samples without a decodable audio
stream are explicitly incomplete joint AV output.

Every file request resolves the path again inside the originating Run directory and
both its recorded output root and the current Settings output root. Junction/symlink
escapes, arbitrary paths and identities belonging to another Run are rejected.
Changing the configured root blocks access to old locations until the original
root is restored. Moving an output does not silently reassign its provenance.

[Delete run](runs-lifecycle.md#deleting-a-run) removes a Run's outputs and its index entry with it,
so download a LoRA you want to keep first. While a delete is unfinished, the Run offers no
outputs. Comparison lists neither deleted Runs nor Runs being deleted; comparing with a Run
deleted from another tab since the list loaded reports that it was not found.

Under Compare with another Run, Compare inputs displays the changed Job
controls/revisions, Settings, dataset/caption manifests, model identities and
upstream/environment provenance of the two Runs' frozen inputs.
It compares recorded facts and offers no quality ranking. Comparing changes no Job or
Run.

A training resumed in segments lists every segment's checkpoints and samples together, in
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
that has no generated samples remains comparable. Each side's final LoRA download
belongs to that side's Run. On narrow screens the two sample panels stack.

One command creates **two synthetic Runs per model** and launches their real media
output browser, without model weights or GPU work:

```powershell
.venv\Scripts\python.exe -m musubi_gui.output_fixture .cache\output-demo --launch
```

Use a new destination; existing directories are preserved. Open the local URL on
port 8768, choose Runs, and inspect a Wan and H3 fixture. Play their videos, the H3
audio sample and the still image; download a checkpoint. Compare revision one with
revision two and inspect changed rank, caption, model identity and upstream version.
Select and play a sample on each side without leaving comparison, then select H3's
audio sample. Check both final LoRA download names against their displayed revisions.
Repeat at 390 px width. Comparing a Run prepared with sampling disabled should show
no generated samples on that side while keeping its recorded input differences usable.
Remove one generated fixture file, refresh, restart the service and confirm its
missing status remains visible. The fixture manifest lists exact paths and Run IDs.
All fixture media is original CC0 synthetic content and its tiny checkpoint is
labelled synthetic, not trained.

Focused verification:

To check pending paired checkpoints manually, run from the repository root in
PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/pair-pending-$([guid]::NewGuid().ToString('N'))"
```

Open a Wan job, apply **Smoke test · Wan alternating · 4 steps**, save and review,
then choose **Prepare run**. Both expert checkpoints should say **Checkpoint pending**
without an incomplete-pair warning. Choose **Start training**; after completion,
both expert download links should appear. This synthetic fixture checks the UI and
orchestration without model weights or GPU training.

```powershell
.venv\Scripts\python.exe -m pytest tests/python/test_outputs_api.py -q
npm.cmd run test:browser -- tests/browser/outputs.spec.ts
npm.cmd run test:browser -- tests/browser/sample-comparison.spec.ts
```

The API tests cover persistent/missing inventory, checkpoint bounds, image/audio/video
delivery and byte ranges, H3 missing audio, comparison, current-root changes, cross-run
identities and a Windows output-folder junction escape. The Edge scenario covers both
models, actual muted video/audio playback, images, download, comparison, file removal
and backend restart. Audible quality, real-model sample quality and new capacity
settings require separate human/hardware acceptance.

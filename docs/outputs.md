# Run outputs and revision comparison

Choose a persistent Run, then use Run outputs to refresh the checkpoint/sample
inventory. Select a file to inspect its originating Run, metadata and availability.
Image, video and audio samples play in the browser; Open output in browser and
Download output use only the recorded file identity. Checkpoint downloads remain
binary attachments. Legacy `.pt`/`.pth`/`.ckpt` files are never unpickled.

The inventory survives restart and retains previously observed missing files.
Missing/moved, unreadable, empty/truncated and blocked outputs have separate status
messages; no source file is deleted or repaired. Refresh after an active producer
finishes writing. An available file means it can be read/decoded or its checkpoint
header/payload bounds can be inspected; it does not establish training quality,
complete optimizer state or resumability. H3 video samples without a decodable audio
stream are explicitly incomplete joint AV output.

Every file request resolves the path again inside the originating Run directory and
both its recorded output root and the current Settings output root. Junction/symlink
escapes, arbitrary paths and identities belonging to another Run are rejected.
Changing the configured root blocks access to old locations until the original
root is restored. Moving an output does not silently reassign its provenance.

Compare frozen run inputs displays changed Job controls/revisions, Settings,
dataset/caption manifests, model identities and upstream/environment provenance.
It compares recorded facts and offers no quality ranking. Original Jobs and Runs
remain unchanged.

One command creates **two synthetic Runs per model** and launches their real media
output browser, without model weights or GPU work:

```powershell
.venv\Scripts\python.exe -m musubi_gui.output_fixture .cache\output-demo --launch
```

Use a new destination; existing directories are preserved. Open the local URL on
port 8768, choose Runs, and inspect a Wan and H3 fixture. Play their videos, the H3
audio sample and the still image; download a checkpoint. Compare revision one with
revision two and inspect changed rank, caption, model identity and upstream version.
Remove one generated fixture file, refresh, restart the service and confirm its
missing status remains visible. The fixture manifest lists exact paths and Run IDs.
All fixture media is original CC0 synthetic content and its tiny checkpoint is
labelled synthetic, not trained.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/python/test_outputs_api.py -q
npm.cmd run test:browser -- tests/browser/outputs.spec.ts
```

The API tests cover persistent/missing inventory, checkpoint bounds, image/audio/video
delivery and byte ranges, H3 missing audio, comparison, current-root changes, cross-run
identities and a Windows output-folder junction escape. The Edge scenario covers both
models, actual muted video/audio playback, images, download, comparison, file removal
and backend restart. Audible quality, real-model sample quality and new capacity
settings require separate human/hardware acceptance.

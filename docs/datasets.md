# Dataset acceptance

After `npm.cmd run build`, run `.venv\Scripts\python.exe -m musubi_gui.dataset_fixture .cache\dataset-demo --launch`.
Use a new destination each time; existing fixture directories are preserved. Open the printed loopback URL
(default `http://127.0.0.1:8767`) and register the printed mixed-media folder in Datasets.
The CC0 fixture contains two images, one video and one deliberately invalid video. No GPU, model weights,
training Python or downloads are required. Media decoding and thumbnails use the application's pinned PyAV/Pillow.

Check the four indexed items, missing-caption coverage, image/video preview and named invalid-media error.
Re-scan unchanged files, restart with `MUSUBI_GUI_STATE` pointing at the fixture's `state` folder, and confirm
the library survives. Source files stay in place. Paths outside Settings' dataset roots are rejected after
symlink/junction resolution. If the source folder disappears, its library entry is retained for recovery.

The gallery renders a bounded window of tiles. Browser codec support depends on Edge; containers accepted
by PyAV may need re-encoding for playback. The original file is preserved when Edge cannot play it.

Captions autosave after 350 ms and flush before selecting another item or dataset. Switching application
sections also flushes the mounted editor. Alt+Left/Right navigates the viewer. Saved appears only after
the backend confirms an atomic `.txt` replacement. Failed writes block item navigation and retain the
draft for retry. External changes produce a conflict: review the current sidecar before explicitly
choosing it or saving the retained draft over that revision. Caption history can restore an earlier
text only against the current revision; concurrent edits are rejected. Original/replacement text is
recorded before writing so interruption evidence stays recoverable in the state directory.

For H3 inspection, add `--h3-audio` to the fixture command. It creates sidecar, embedded,
missing, ambiguous, malformed, short and offset-audio examples alongside the base media.
Open a video and expand **H3 audio and media inspector**. Inspect the chosen source, play
the resolved audio and review the geometry/timeline diagnostics. Set the explicit path to
the fixture's `chosen.wav` to resolve an ambiguous pair; an empty path restores automatic
resolution. **H3 dataset audio coverage** reports included/excluded items and valid audio
supervision. Missing audio is allowed only by an explicit job policy; malformed supplied
audio remains an error. Source media is never repaired or discarded by inspection.

The inspector follows the pinned upstream's explicit → exactly one same-stem sidecar →
embedded → absent precedence, 24 fps frame grid, `17*n+5` frames from 124–345, 32-pixel
geometry, 50 ms audio discontinuity limit and 200 ms missing-audio budget. It decodes media
in an isolated application subprocess with a 45-second timeout. Long clips that exceed it
need shorter source clips. Geometry, timestamps and playback are preflight evidence;
actual upstream caching remains the final authority for crop-specific supervision.

Search names/caption text, sort and filter missing captions, invalid media, exclusions or selected items.
Checkbox selections remain explicit across filters; the counter identifies selected items hidden by the
current filter. Excluding changes library metadata without changing or deleting source media. The
included count is the effective training count. Select **Selection and bulk caption actions** to preview
exact literal prefix/suffix/find/replace changes. Applying the saved preview checks each authoritative
caption revision and reports saved/conflict/failed outcomes independently. Preview again after resolving
a failed/conflicting item. **Restore successful captions** restores only that operation's successful
items and refuses any item edited afterward. Sidecar history remains available for individual recovery.

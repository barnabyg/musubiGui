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

For uploads, use the same fixture command and choose **Create managed dataset** with the printed folder
as its storage root. Upload the fixture's `red.png`/`red.txt` and `invalid.mp4`; expect the media/caption
to appear immediately and the invalid file to fail independently. Upload the same filename again to
see an explicit collision. Restart and reopen the managed dataset to confirm persistence.

Alternatively, drag one or more local images/videos onto the labelled drop area in the
managed dataset's upload panel. Matching captions and audio sidecars can be dropped together
with their media. Dropping files selects a replacement queue; choose **Upload selected files**
to start. The file picker remains available for keyboard selection. During an upload,
both selection methods are disabled; pause or finish before choosing another batch.
Directory import and remote URL drops are not supported.

For a manual drag check, use Windows File Explorer to drag `red.png`, `red.txt` and
`motion.mp4` over the area. Check its highlight remains while crossing the text/file picker,
clears when leaving, and clears after dropping without opening a file in the browser.
Upload and confirm image/video gallery entries and the red caption. Drop an unsupported file
and a duplicate filename to check the existing per-file errors. While uploading, attempt
another drop and confirm the active queue is preserved. Also check the file picker with Tab.

Uploads use bounded 512 KiB JSON chunks and private `.part` files. A pause, network failure or disk error
preserves the partial upload; select the same file and retry to verify the existing byte prefix before
resuming. A changed prefix is rejected. **Cancel partial** removes only that session's temporary file;
completed uploads are retained. Media/container types and UTF-8 captions are checked before an exclusive
hard-link publication inside the managed root. The destination filesystem must support local hard links
(for example NTFS). Existing destinations are never overwritten. Media is limited to 64 GiB per file,
captions to 2 MiB, and long media inspection has the same 45-second decoder limit. Captions require an
already uploaded matching media stem; the browser orders caption uploads after media automatically.

Dataset item paths and caption sidecars must remain regular local files/directories. The application
rejects links/reparse-point redirection even when a target stays inside an allowed root, so replacing a
folder with an inside-root junction cannot redirect an already opened item's caption to another file.
Register the physical folder or restore regular files before continuing; linked targets are preserved.

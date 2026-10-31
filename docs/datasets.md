# Dataset acceptance

Dataset roots default to the `datasets` folder under the application project root
(the working directory selected by `launch.cmd` / `run.bat`). The Dataset folder
field starts with the first configured root. Creating a managed dataset creates
the root folder when needed. Saved custom roots, including an explicitly empty
list, are preserved; change them in Settings to use a different location.

The Datasets library lists registered and managed datasets, their media/caption
counts, storage locations and edit/delete actions. Choose **New dataset** to open
the creation screen, then choose **Use existing folder** to register a folder as it is, or **Create a new folder for uploads** to create a managed dataset.
Both flows open the individual dataset editor. **Back to datasets** returns to
the library after saving the active caption; a failed save keeps the draft open.

The individual editor fills the available window with thumbnails and editable captions.
A compact top bar contains back navigation, the dataset name/scan status, **Add media**,
**Search & edit** and **Dataset settings**. Thumbnails start directly below this bar; the
workspace banner and large page heading are omitted on this screen.
Type directly in the caption field beneath any thumbnail. Each field autosaves after
350 ms and saves immediately when focus leaves it, with a per-caption save indicator.
Select the thumbnail to open its full preview, larger caption editor and recovery
history; both editors share the same draft and revision. **Close preview** saves
pending captions and returns to the full gallery. **Search & edit** opens a side panel containing
search, filters, sorting, selection counts and bulk caption actions. **Dataset settings**
opens a side panel holding the editable display
name, storage path and H3 coverage. Renaming changes the library name without moving
the folder. Opening a panel keeps the gallery's rows and columns and narrows the tiles. The preview
changes the column count, but the active tile stays in view when it opens or closes. Below
1000 px the preview sits above the gallery, so another item can be chosen without closing it.
**Add media** opens the managed upload panel; it starts open for a new
managed dataset and stays open while files upload. Closing or switching panels keeps
uploads and tool state intact. Panels and the preview overlay the gallery on narrow
windows, each with its own close control. A registered folder is populated by adding
files on disk and choosing **Re-scan** in **Dataset settings**.

**Delete** requires confirmation naming the dataset and removes its library entry
and indexed items only. Original and uploaded media, caption sidecars, thumbnails,
caption history and bulk/upload recovery evidence remain on disk. Jobs referencing
the deleted entry need a dataset selected again. Re-registering the preserved folder
creates a new entry with a new identity; prior exclusions and history are not attached
to that new entry. Deletion waits for a pending or active scan to finish.

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

Scrolling a card out of view keeps its draft and any pending save. A focused caption
stays in place while its saved text changes search/filter/sort results; those results
update when focus leaves the card. Failed saves retain the draft after scrolling and
block leaving the dataset or applying bulk/upload operations. Use **Review caption**
on the affected card, or **Review unsaved captions** in the top bar, to open the full
editor for retry, conflict resolution and history. Closing the browser with unsaved
captions requests confirmation.

Captions autosave after 350 ms and all pending captions flush before selecting another item or dataset. Switching application
sections also flushes pending captions. Alt+Left/Right navigates the viewer. Saved appears only after
the backend confirms an atomic `.txt` replacement. If another program has the sidecar open, the save
keeps trying for two seconds. Failed writes block item navigation and retain the
draft for retry. External changes produce a conflict: review the current sidecar before explicitly
choosing it or saving the retained draft over that revision. Caption history can restore an earlier
text only against the current revision; concurrent edits are rejected. Original/replacement text is
recorded before writing so interruption evidence stays recoverable in the state directory.

For H3 inspection, add `--h3-audio` to the fixture command. It creates sidecar, embedded,
missing, ambiguous, malformed, short and offset-audio examples alongside the base media.
Open a video and expand **H3 audio and media inspector**. Inspect the chosen source, play
the resolved audio and review the geometry/timeline diagnostics. Set the explicit path to
the fixture's `chosen.wav` to resolve an ambiguous pair; an empty path restores automatic
resolution. Under **Dataset settings**, **H3 dataset audio coverage** reports included/excluded items and valid audio
supervision. Missing audio is allowed only by an explicit job policy; malformed supplied
audio remains an error. Source media is never repaired or discarded by inspection.

The inspector follows the pinned upstream's explicit → exactly one same-stem sidecar →
embedded → absent precedence, 24 fps frame grid, `17*n+5` frames from 124–345, 32-pixel
geometry, 50 ms audio discontinuity limit and 200 ms missing-audio budget. It decodes media
in an isolated application subprocess with a 45-second timeout. Long clips that exceed it
need shorter source clips. Geometry, timestamps and playback are preflight evidence;
actual upstream caching remains the final authority for crop-specific supervision.

Open **Search & edit** to search names/caption text, sort and filter missing captions, invalid media, exclusions or selected items.
Checkbox selections remain explicit across filters; the counter identifies selected items hidden by the
current filter. Excluding changes library metadata without changing or deleting source media. The
included count is the effective training count. Select **Selection and bulk caption actions** to preview
exact literal prefix/suffix/find/replace changes. Applying the saved preview checks each authoritative
caption revision and reports saved/conflict/failed outcomes independently. Preview again after resolving
a failed/conflicting item. **Restore successful captions** restores only that operation's successful
items and refuses any item edited afterward. Sidecar history remains available for individual recovery.

For uploads, use the same fixture command and choose **Create a new folder for uploads** with the printed folder
as its storage root. Upload the fixture's `red.png`/`red.txt` and `invalid.mp4`; expect the media/caption
to appear immediately and the invalid file to fail independently. Upload the same filename again to
see an explicit collision. Restart and reopen the managed dataset to confirm persistence.

Alternatively, drag one or more local images/videos onto the labelled drop area in the
managed dataset's upload panel. Matching captions and audio sidecars can be dropped together
with their media. Dropping files starts their upload immediately, replacing the prior queue.
The file picker remains available for keyboard selection; choose **Upload selected files**
to start files selected through that picker. During an upload,
both selection methods are disabled; pause or finish before choosing another batch.
Directory import and remote URL drops are not supported.

For a manual drag check, use Windows File Explorer to drag `red.png`, `red.txt` and
`motion.mp4` over the area. Check its highlight remains while crossing the text/file picker,
clears when leaving, and clears after dropping without opening a file in the browser.
Confirm uploads start without another click, followed by image/video gallery entries and the red
caption. Drop an unsupported file and a duplicate filename to check the existing per-file errors. While uploading, attempt
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

# Dataset acceptance

## Preparing video sources for H3

Open a source dataset, choose **Dataset settings → Prepare videos for H3**, and
choose **Prepare H3 dataset**. Upload a batch into a dataset first, or register an
existing folder and wait for its scan to finish. Preparation uses all included
videos, regardless of gallery selection or filters; images and excluded videos
are left out.

The default is **640 × 352, 124 frames at 24 fps (5.167 seconds)**, the configuration
demonstrated on the target 24 GB GPU. Larger resolutions or durations can still
exceed training VRAM. Width and height must be multiples of 32; duration is selected
from 124–345 frames in increments of 17. Source frame rates can differ.

Each source produces one clip from its beginning. Choose centre cropping to fill
the frame, or fit with black borders to retain the whole picture. Sources shorter
than the chosen duration are skipped, with a per-file explanation. In particular,
an exactly five-second source is too short for the shortest supported window.
The tool does not pad video or split a source into multiple clips.

Preparation creates a fresh managed dataset under **Prepared dataset parent**.
This parent must be within a configured dataset root and outside the source
dataset. If the source is an entire configured root, add a separate output root
in Settings first. Originals and their captions remain in place. Matching captions
are copied; review them because trimming can remove events described in the text.
Output names include an item identifier to distinguish same-stem sources.

Audio selection follows the inspector: explicit path, one same-stem sidecar, then
embedded audio. Supplied audio is aligned to the video and written as a matching
32 kHz stereo WAV. Materially short, ambiguous, malformed or discontinuous audio
is reported per file; it is never silently dropped. Small missing regions within
H3's 200 ms budget are filled with silence. **Allow videos without audio** accepts
sources with no audio and leaves their output audio absent; choose the training
job's allow-missing audio policy for those datasets.

The panel reports progress and failures while successful files continue. Use
**Open prepared dataset** when complete. Reloading the panel recovers the latest
batch report. Interrupted batches retain completed copies; start a new batch to
retry, and re-scan a retained partial dataset before using its completed clips.
Conversion runs on the CPU, needs no model weights or separate FFmpeg install,
and limits each conversion subprocess to 15 minutes. Source inspection has the
existing 45-second media probe limit; very long sources may need shorter clips.

Manual verification: from `C:/docs/git/musubiGui`, run this one PowerShell 7 command
(prerequisites: Python 3.12, Node.js 24 LTS, uv, and a source video at least six
seconds long; the launcher installs dependencies and builds the app):

```powershell
.\launch.cmd
```

Choose **Datasets → New dataset → Upload files**, create a folder, and upload the
video and its matching caption. Open **Dataset settings → Prepare videos for H3**.
Keep the defaults, using the configured dataset root as the output parent. For a
silent source, enable **Allow videos without audio**. Prepare, then open the new
dataset, play its video and inspect its H3 media. Expect 640 × 352, 124 frames,
24 fps and 100% audio coverage when audio is supplied. The original video, audio
and caption must remain unchanged. This verifies conversion and playback, not
GPU learning quality. Repeat with a five-second video: expect a per-file
too-short explanation while the longer clip still succeeds.

## Dataset library

Dataset roots default to the `datasets` folder under the application project root
(the working directory selected by `launch.cmd`). Saved custom roots, including an
explicitly empty list, are preserved; change them in Settings to use a different
location.

The Datasets library lists registered and managed datasets, their media/caption
counts, storage locations and edit/remove actions. Choose **New dataset** to open
the creation screen. Choose **Upload files** or **Use existing folder** before
entering a path. Uploads use the first configured root as their **Upload parent
folder**, so a name is enough; inspect or change the parent before choosing
**Create a new folder for uploads**. This creates a separate `managed-…` subfolder
and shows its final path in the dataset editor. The parent is created if needed.

**Use existing folder** starts with an empty **Dataset folder**. Enter or Browse
to the exact folder to index, including its subfolders. Selecting an entire
configured root requires an acknowledgement because it can include sibling
datasets. If the folder is already registered, open the existing entry instead;
its identity and indexed state are preserved. Existing valid root registrations
remain usable. If no root is configured, **Set a dataset root in Settings** opens
Settings; save a root, then use browser Back to return to the retained creation form.
Both flows open the individual dataset editor. **Back to datasets** returns to
the library after saving the active caption; a failed save keeps the draft open.

Each library entry shows its worked-out contents, such as `12 images`, `3 videos` or
`12 images · 3 videos`. These count valid media that isn't excluded, the same items as the
included count, and follow each re-scan. A dataset has no stored image or video type, and
creating one asks for none, so a mixed folder works like any other. Keeping images and videos
in separate datasets lets each be reused with models that take only one of them, and a
[job can list several](jobs.md#training-sections).

For image-only, video-only, and separate image-plus-video preparation examples,
including a single command that launches testable jobs without model downloads,
see [Preparing images, videos, or both](jobs.md#preparing-images-videos-or-both).

The individual editor fills the available window with thumbnails and editable captions.
A compact top bar contains back navigation, the dataset name/scan status, **Add media**,
**Bulk actions** and **Dataset settings**. Thumbnails start directly below this bar; the
workspace banner and large page heading are omitted on this screen.
Type directly in the caption field beneath any thumbnail. Each field autosaves after
350 ms and saves immediately when focus leaves it, with a per-caption save indicator.
Select the thumbnail to open its full preview, larger caption editor and recovery
history; both editors share the same draft and revision. **Close preview** saves
pending captions and returns to the full gallery. Search, filter, sort and a caption count such as `2 of 4 captioned`, which shows only the missing ones when chosen, stay on the top bar. **Bulk actions** opens a side panel containing
selection counts and bulk caption actions. **Dataset settings**
opens a side panel holding the editable display
name, storage path and H3 coverage. Renaming changes the library name without moving
the folder. Opening a panel keeps the gallery's rows and columns and narrows the tiles. The preview
changes the column count, but the active tile stays in view when it opens or closes. Below
1000 px the preview sits above the gallery, so another item can be chosen without closing it.
At 550 px or narrower, the preview fills the workspace with media, caption and
previous/next controls in one scroll pane; close it to return to inline gallery editing.
**Add media** opens the managed upload panel; it starts open for a new
managed dataset and stays open while files upload. Closing or switching panels keeps
uploads and tool state intact. Panels and the preview overlay the gallery on narrow
windows, each with its own close control. A registered folder is populated by adding
files on disk and choosing **Re-scan** in **Dataset settings**. When repairing job
inputs, the editor requests `POST /api/datasets/{id}/scan?wait=true` so returning
to the job uses the refreshed index. Omitting `wait` starts a background scan.

**Pause uploads** pauses remaining files. A file already submitted for validation
finishes publication and scanning before the job can review the dataset again.

**Remove from library** requires confirmation naming the dataset and removes its library entry
and indexed items only. Original and uploaded media, caption sidecars, thumbnails,
caption history and bulk/upload recovery evidence remain on disk. Jobs referencing
the removed entry need a dataset selected again. Re-registering the preserved folder
creates a new entry with a new identity; prior exclusions and history are not attached
to that new entry. Deletion waits for a pending or active scan to finish.

**Dataset settings** and **Bulk actions** list the saved jobs using the dataset,
including their family and revision, with links to active drafts and an explicit
archived status. If no saved jobs use it, the list says so. Inclusion changes and
caption edits affect these jobs' next review and preparation; prepared Runs keep
their frozen media, captions and outputs. Exclude selection remains one click;
keep the selection and choose **Include selection** to reverse it. Removal shows
the same affected jobs in its confirmation, with links opening in a new tab so
the confirmation stays available. A failed job lookup must be retried before
changing inclusion or removing the entry.

To verify the shared scope, run this from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Prepare WAN acceptance 1, then open WAN acceptance's **Bulk actions**. Both WAN
acceptance jobs should be linked. Select motion.mp4, exclude it and review both
drafts: neither has usable included media. Include it again to restore their
input. Open **Remove from library**, inspect the named jobs and cancel. Removing
the entry preserves motion.mp4 and its caption on disk, and the prepared Run can
still start and provide its LoRA from the saved snapshot. This is a CPU fixture;
it does not establish GPU model quality.

After `npm.cmd run build`, run `.venv\Scripts\python.exe -m musubi_gui.dataset_fixture .cache\dataset-demo --launch`.
Use a new destination each time; existing fixture directories are preserved. Open the printed loopback URL
(default `http://127.0.0.1:8767`) and register the printed mixed-media folder in Datasets.
The CC0 fixture contains two images, one video and one deliberately invalid video. No GPU, model weights,
training Python or downloads are required. Media decoding and thumbnails use the application's pinned PyAV/Pillow.

The command also prints a separate image folder and video folder, for combining media in one
[job](jobs.md#datasets-in-a-job). Check the four indexed items, missing-caption coverage, image/video preview and named invalid-media error.
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
sections also flushes pending captions. The preview shows its Alt+Left/Right shortcuts.
Consecutive previous/next actions move through the filtered list and focus the next
caption, or the preview itself if that sidecar cannot be edited. A failed save keeps
the original draft and returns focus to its caption. Closing the preview returns
focus to its gallery tile, or the gallery if that tile is no longer available.
Saved appears only after
the backend confirms an atomic `.txt` replacement. If another program has the sidecar open, the save
keeps trying for two seconds. Failed writes block item navigation and retain the
draft for retry. External changes produce a conflict: review the current sidecar before explicitly
choosing it or saving the retained draft over that revision. Caption history can restore an earlier
text only against the current revision; concurrent edits are rejected. Original/replacement text is
recorded before writing so interruption evidence stays recoverable in the state directory.

To check preview navigation, run this from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Open **Mixed media and invalid item**, preview blue.png and type a caption.
Press Alt+Right twice: invalid.mp4 and then motion.mp4 should open, each with its
caption focused. Press Alt+Left twice and confirm blue.png retained the saved text.
Close the preview and expect its tile to have keyboard focus. Repeat at 390 px wide:
the preview should occupy one pane with reachable caption and navigation controls.
For a conflict check, edit the active `.txt` outside the browser, type different text
in its caption, and choose Next or Close preview. Expect the original item, draft and
caption focus to remain until the conflict is resolved.

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

Use the top bar to search names/caption text, sort and filter missing captions, invalid media, exclusions or selected items. Tiles badge missing captions, exclusions and invalid media.
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

### Check upload and registration locations

From the repository root in PowerShell 7, start an isolated CPU fixture:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Choose **New dataset**, enter a name and create an upload dataset using the
default parent. Expect a separate managed subfolder, with its final path visible
in **Dataset settings**. Start another dataset and select **Use existing folder**
with the keyboard. Browse to a configured root; expect a warning and an explicit
acknowledgement before registration. Try an already registered folder and expect
an **Open existing dataset** link. Try a child folder and check that only its
exact path and descendants are indexed. With an empty Dataset roots setting,
follow the Settings link, save a root and use browser Back; expect the name and
method to remain in the creation form.

### Resolve a caption before preparing a Run

Jobs waits for caption saves from this tab in any selected dataset. Both prepare actions
stay disabled if a save fails. Follow **Resolve caption** to open the retained draft.
**Review current sidecar** shows the disk text. Choose **Use current sidecar** to train on
that text, or **Save my text over this revision** to save the draft. Jobs using other
datasets remain available.

From the repository root, start the isolated CPU fixture:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Open **WAN acceptance** in Datasets. After the gallery loads, change its
`motion.txt` outside the browser, then edit the caption in the gallery.
Open **WAN acceptance 1** in Jobs and review it. Expect both prepare actions
to stay disabled and the repair link to reopen your draft. Resolve the conflict,
return to the job, review and prepare. The Run’s frozen inputs should contain
the text you chose.

### Gallery loading and sidecar checks

Opening a gallery reads each item's current caption once, without computing library
counts again. Location validation is shared within the request; each media and sidecar
path is still checked for containment and link/junction redirection. Thumbnail access
and saving one caption do not read other items' captions. External edits remain
authoritative, and a stale save still requires conflict resolution.

For a manual check, run this command from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Copy the fixture's `blue.png` into a child folder under its configured dataset root
using 64 distinct names, each with a matching nonempty `.txt` caption. Register that
folder and wait for its scan to complete. Return to the library, reopen the gallery,
and measure until the first caption is editable. Expect thumbnails and captions to
appear together; edit one caption and verify its sidecar. Change another caption
outside the application, reopen, and expect its new text. Repeat with 8 and 32 files.

The real Edge test `tests/browser/dataset_gallery.spec.ts` repeats these sizes and
prints three HTTP item-list timings and one library-click-to-caption timing per size.
On Windows 11 on 9 November 2026, the measurements below compared base `eb7656c`
with the request-local location change. Each cell is milliseconds; scans had already
completed. The gallery timing includes library refresh and rendering, and the library
accumulates the earlier test datasets. These are observations, not performance limits.

| Items | API before (three requests) | API after (three requests) | Gallery before | Gallery after |
| ----- | --------------------------- | -------------------------- | -------------- | ------------- |
| 8     | 42 / 41 / 45                | 10 / 9 / 10                | 117            | 56            |
| 32    | 172 / 171 / 165             | 25 / 25 / 27               | 343            | 103           |
| 64    | 307 / 315 / 290             | 45 / 47 / 44               | 833            | 218           |

The base already included direct location lookup, so it did not reproduce the older
quadratic timings reported in #120. Removing the remaining redundant count pass and
per-item location lookups reduced gallery sidecar reads from twice to once per item.
The HTTP regression checks exact filesystem read counts at 8, 32 and 64 items instead
of enforcing machine-specific time limits. The resulting item-list timings scale
approximately linearly; browser timings also include fixed interaction costs.

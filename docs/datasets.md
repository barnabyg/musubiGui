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

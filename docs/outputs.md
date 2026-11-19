# Run outputs

Open **Stage logs and diagnostics** in Run Overview. Select the output folder path and
paste it into File Explorer. Collect LoRAs directly from the Run folder. A checkpoint
and its saved-state folder use the frozen Job name and revision, for example
`My-Job-r2.safetensors` and `state/My-Job-r2-state/`. Alternating Wan filenames identify
each expert and its step or phase.

The Run creates subfolders as it needs them.

| Subfolder  | Contents                                                             |
| ---------- | -------------------------------------------------------------------- |
| `media/`   | Frozen training media, captions and any separate audio               |
| `samples/` | Generated media, including native previews under `samples/sample/`   |
| `state/`   | Optimizer and training states used to resume                         |
| `config/`  | Frozen inputs, dataset configuration, preview prompts and provenance |
| `logs/`    | Stage logs, timing and performance reports, and training bookkeeping |

Retries and resumed segments use the same layout.

On **Samples**, select a generated image, video or audio file to view it, inspect its
metadata or download it. Files the browser cannot preview still offer a download.
**Refresh outputs** rescans the inventory and lets you retry playback. A training resumed
in segments lists samples from its segments in step order. The output path under
**Stage logs and diagnostics** belongs to the selected Run.

The inventory survives restart and keeps previously observed missing files. It refreshes
as the Run or its stages change, preserving the selected output and playback.
Output warnings such as incomplete Wan pairs appear in Overview. Sample-specific
errors appear when you select the sample. H3 video samples without a decodable audio
stream appear as incomplete joint audio/video output.

Every file request resolves the path inside the originating Run directory and both its
recorded and current Settings output roots. Changing the configured root blocks access
to old locations until the original root is restored. [Delete run](runs-lifecycle.md#deleting-a-run)
removes the Run's outputs and index entry. Copy files you want to keep first.

## Output and comparison APIs

`GET /api/runs/{id}/outputs?chain=true` lists checkpoints and samples with their global
`step` and `segment` (`run_id`, `position`, `revision`, `status`). It returns
`segments` and `latest` for the training. Each file is served by the Run that made it.
Without `chain`, a segment lists its own files.

`GET /api/runs/{id}/compare/{other_id}` reports differences between the two saved Runs'
recorded settings, dataset manifests and provenance.

## Manual verification

From the repository root in PowerShell 7, launch the isolated CPU fixture.

```powershell
.\acceptance.cmd --workspace ".cache/paired-outputs-$([guid]::NewGuid())" --wan-paired
```

Review and start a seeded Wan Job. Open its completed Run and expand **Stage logs and
diagnostics**. Paste the output path into File Explorer and verify the low- and high-noise
checkpoint files are directly in the Run folder, with saved states under `state/` and
generated videos under `samples/`. Choose **Samples**, play the generated video and use **Refresh
outputs**. Expect the selected video to stay selected and keep playing. Repeat at a narrow
viewport.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/python/test_outputs_api.py -q
npm.cmd run test:browser -- tests/browser/outputs.spec.ts tests/browser/run-outputs.spec.ts
```

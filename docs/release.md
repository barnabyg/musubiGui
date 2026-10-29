# Windows release acceptance

Target: native Windows 11, RTX 3090 with 24 GB VRAM, the existing nominal **96 GB**
RAM machine (95.6 GiB usable) and at least 400 GB available disk. The previous
64 GB requirement was corrected by the user. Scope is LoRA for Wan 2.2 A14B T2V
and MiniMax H3 T2VA, with manually edited `.txt` captions.

## Start the isolated acceptance workspace

Prerequisites: Python 3.12, uv, Node.js 24 LTS, Git for Windows and Microsoft Edge.
From a clean checkout, run:

```powershell
.\acceptance.cmd --workspace .cache\acceptance-1
```

This installs only the locked application dependencies, builds the UI, creates a
new isolated workspace and launches the loopback app at `127.0.0.1:8767`. Existing
workspaces are refused. Use another `--port` for a concurrently active instance.
`--prepare-only` creates the fixtures without launching the backend.

The default is explicit **synthetic CPU fixture mode**. It creates an original
CC0 image/video/audio library with an invalid item, separate Wan/H3 training
datasets, four saved job drafts, tiny labelled fake weights and a dedicated fake
checkout with its own actual Git revision. Its subprocesses exercise the normal
worker stages, logs, queue, outputs and saved-state files without Torch or a GPU.
No fixture result establishes model support. Nothing is downloaded into or
modified in the existing training installation.

`acceptance.json` records the workspace, state directory, mode and job identities.
Application state and outputs remain in that workspace. Retain it for diagnosis;
delete it only after stopping its backend/workers and preserving needed results.

To reopen a fixture workspace without recreating it:

```powershell
$env:MUSUBI_GUI_STATE = (Resolve-Path .cache\acceptance-1\state).Path
.venv\Scripts\python.exe -m musubi_gui --port 8767 --acceptance-fixture
```

The synthetic checkout marker alone does not enable fixture execution in a
normal application launch; the explicit fixture opt-in is required.

## GUI scenarios and expected results

1. Browse the mixed library, preview image/video, inspect the invalid item and
   re-scan. Edit a caption, navigate rapidly and inspect the corresponding `.txt`.
   Saved appears only after the write is confirmed. Make an external edit to
   exercise conflict handling and history restoration.
2. Filter/select items, exclude one and preview a bulk prefix/find/replace edit.
   Apply it, verify unselected sidecars are unchanged, then restore the intended
   revisions. Register a managed folder inside the configured dataset root and
   upload media plus captions; verify collisions/invalid files are reported.
3. Inspect H3's chosen same-stem WAV source, geometry, timeline and audio coverage.
   Open Wan and H3 drafts, change a valid control, save/reopen, review paths and
   generated native settings. Duplicate a draft and try presets/import/export.
   Cancel a model conversion and confirm the original remains unchanged.
4. Prepare a job, inspect each cache stage and logs, then prepare the same inputs
   again to inspect reuse. Change a source caption and prepare again; the prior
   Run's immutable snapshot must remain unchanged. Edit the draft while its Run
   is active and confirm the active arguments retain their original revision.
5. Start the prepared Run, close/reopen the browser, and confirm persistent logs,
   checkpoint and sample output. Queue other jobs, reorder/cancel pending entries
   and verify only one GPU operation is owned at a time. Restart the service and
   inspect preserved entries and reconciliation before launching more work.
6. Request stop and inspect the observed process state. Force termination needs a
   separate explicit confirmation. Retry failed preparation after removing a
   deliberate `checkout\fail-latent_cache` marker. A `fail-train` marker exercises
   training failure without modifying Python source.
7. With saved state enabled, restore a complete saved state as a **new segment**.
   Incomplete state must be rejected. The pinned upstream restores optimizer
   state but resets its reported training-step counter and does not restore the
   data position; this is not seamless step/data continuation. Initializing a new
   Run from LoRA weights is distinct from restoring optimizer state.
8. Browse outputs from both model families, play video/audio, compare Run
   revisions and then remove a fixture sample on disk to inspect the missing
   output state. File delivery must remain within the originating output root.

## Real GPU acceptance

An existing musubi-tuner checkout at revision
`f8a1b03794a49239a3539015075f5123d6c07d66`, its existing Python/environment and the
compatible model components documented in [reference workflows](reference/README.md)
are required. No training dependencies or models are installed by the app.
The tested environment is Python 3.12.10, PyTorch 2.13.0+cu130, CUDA 13.0 and
musubi-tuner 0.3.6. Select exact existing component paths in Settings; Wan may use
the separately validated [offline weight conversion](reference/wan-conversion.md).
Fresh [native-weight qualification](reference/wan.md#native-weight-qualification)
also passed with the user-supplied FP16 experts and native T5 encoder on 27 October 2026. It exercised the final atomic Windows launcher through real GUI preparation,
training, playback and optimizer-state restoration from step 2 to 3. A separate
high-noise-only CLI training/generation/playback scenario passed. The GUI continues
to expose its low-noise recipe; combined-expert training remains unqualified.

```powershell
.\acceptance.cmd --workspace .cache\gpu-acceptance-1 --mode gpu --settings C:\absolute\settings.json
```

This prepares original CPU media and saved drafts using the existing training
installation. It never starts GPU work automatically. Review, Prepare and Start
one model at a time through the GUI. Expected short results are completed latent
and text caching, a two-step checkpoint, and a playable sample; H3 must include
audio. Save state, restore it as a new segment and inspect optimizer-step
continuation separately from the reset displayed counter. Retain Run arguments,
environment, manifest, logs, measurements, checkpoint/sample identities and media
decode/playback evidence. Synthetic worker results cannot fill this evidence gap.

The committed [GUI acceptance evidence](reference/evidence/gui-acceptance.json)
records the earlier converted-Wan/native-H3 preparation, training, checkpoint saving, Edge sample playback and
optimizer-state restoration for both models on the corrected target machine.
Both optimizers advanced from step 2 to 3 after restoration as new segments. The
additional [native Wan evidence](reference/evidence/wan-native.json) records fresh
native-file results separately. The earlier converted-weight Wan run
produced a 153,492,464-byte final checkpoint and a 1.0625-second video; H3 produced
a 74,617,608-byte checkpoint and a 5.167-second video with decoded audio.

| Model | Preparation (s) | Training + sample (s) | Restore + sample (s) | Retained run/cache (GB) |
| ----- | --------------: | --------------------: | -------------------: | ----------------------: |
| Wan   |            54.6 |                 155.4 |                160.0 |                    3.09 |
| H3    |            74.7 |                 172.3 |                 89.1 |                    1.51 |

Restoration peaked at a sampled 8.47 GiB device VRAM / 19.32 GiB process-tree RSS
for Wan and 10.48 GiB / 33.87 GiB for H3. These sampled values can miss short peaks;
whole-device and host measurements include other applications. Retained storage
excludes the existing/derived source weights. The original Wan GUI run completed
in a preceding harness attempt: its durable stage timings survived, while its
peak telemetry did not. Separate reference and restoration measurements remain.
Playback was muted; audible quality and useful learning were not human-reviewed.
Two steps and four inference steps do not establish production capacity. The later
[production-size trial](reference/evidence/production-trial.json) qualified Wan at
832×480 × 33 frames, rank 16 and H3 at 640×352 × 124 frames, rank 8 on the target PC;
these are the Measured presets described in [Jobs](jobs.md). Useful learning and H3
audio quality still need a human review of a full-length training run.

The [clean-checkout record](reference/evidence/clean-checkout.json) confirms the
documented launch command installed locked app dependencies, served UI assets and
the API, and prepared all four synthetic acceptance jobs. Its application
environment contains no Torch. The canonical command below remains the complete
automated release gate.

## Verification, compatibility and distribution

Run `npm.cmd run verify` on the integrated tree. Its format, lint, strict types,
static analysis, Python/browser tests, lock/dependency/vulnerability/secret checks
and wheel/sdist validation must all pass without unresolved warnings. See
[verification](verification.md) for the canonical gates and narrow existing
exceptions. Browser/API/filesystem tests cover critical persistence and process
flows; released version-one settings, jobs and configuration exports remain
readable. Unknown future versions are preserved and refused.

Verify `launch.cmd --no-browser` and the acceptance command from a clean checkout
using declared prerequisites, then inspect packaged file lists. Distributables
contain application source and built UI, not local model weights, datasets,
credentials, logs, environments or runtime state. Generated assets remain ignored.
AI Toolkit supplies interaction inspiration; no source/assets are copied.
musubi-tuner remains a separate installation and retains its own licence.
Model weights retain their model-card licences, including the MiniMax H3
Community License. Original synthetic fixture media/captions are CC0 1.0.

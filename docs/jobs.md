# Job drafts and configuration review

## Drafts and revisions

Launch with `launch.cmd`, register a dataset, and open Jobs. Jobs are listed in a table
of name, model, dataset, revision, last Run and last edited time, most recently edited
first; choose a heading to sort by it. New Wan job creates an incomplete saveable draft
with a name no other job is using, ready to retype. Select its dataset and edit the grouped preparation,
LoRA, memory, checkpoint and sample controls. Save draft, Review configuration, Prepare
run and the status stay visible in a bar as you scroll. Duplicate job, Change model,
Archive job, Delete job and Import / export configuration are in the bar's ⋯ menu.
Save draft creates a new immutable
revision; reopening the application retains every supported value. An outdated tab
cannot overwrite a newer revision.
Revision allocation is serialized across backend processes sharing the same state
directory, including saved drafts, confirmed model conversions and preset application.
The version-one revision JSON files remain unchanged.
Interrupted writes retain their temporary files; only published revisions appear
in the library. Unknown storage remains blocked and preserved for repair.

## Configuration review

Review configuration shows effective included videos, resolved component/output
paths, native dataset TOML and each upstream argument array. Errors block preparation
while the draft remains editable. Component overrides must stay inside the configured
model roots. The GUI's Wan recipe trains the low-noise expert. A separate
[native-weight CLI acceptance scenario](reference/wan.md#native-weight-qualification)
also passed high-noise-only training; that recipe is not exposed by the GUI.
Combined training remains unqualified. Runtime FP8 scaling requires native FP16 sources,
not saved scaled-FP8 weights. Inactive-DiT offload is not supported by this recipe.

## Disk estimates

Review also estimates the disk a Run will use: the media snapshot, caches, LoRA
checkpoints, optimizer states and samples, plus the free space on each target volume.
When the output and cache roots share a volume, their needs are added together. A Run
needs the estimate plus a margin of 10% or 1 GB, whichever is larger.

If a volume is short, review shows a warning naming the volume and the shortfall. It is
only a warning: Prepare still goes ahead, because the estimate can be wrong in either
direction and large runs are normal. A Run that needs tens of GB does not warn when the
space is free.

The estimate uses sizes measured on the target PC
([Wan](reference/wan.md#measured-disk-use), [H3](reference/h3.md#measured-disk-use)):

- Checkpoints are linear in rank for each model. Each saved state is 3.0014 times its
  checkpoint.
- The save count is `floor(steps / save_every) + 1`. This is the peak: when the last step
  falls on the interval, the duplicate exists until training succeeds and it is removed,
  so a successful Run keeps one set fewer.
- Wan latents are computed from size and frames, and H3 latents are fitted to two measured
  sizes at 124 frames. Each cached video window or image adds one latent file, and repeats
  add none.

Its limits:

- It covers one Run and does not count other queued Runs. Only review shows it, so Retry
  and Restore as new segment don't warn on low disk.
- H3 frame counts above 124, H3 ranks other than 4 and 8, and Wan ranks other than 8 and
  16 are extrapolated.
- Text caches use the mean per-item size measured in the #26 trial, so long captions use
  more. Samples use the largest measured bytes per pixel-frame, and video content changes
  the real size.
- Bucketing can change latent sizes.
- Compiler caches are left to the margin.
- Free space is read at review. It is not checked again at Prepare or Start, so a Run can
  still run out if the space is used in the meantime.

Compared with run `fb66e3d3` in #26 (Wan, 832×480, 33 frames, rank 16, 200 steps, saving
every 100 with state), the estimate gives 83 MB of caches against 83 MB measured, 921 MB
of checkpoints against 921 MB, and 2.76 GB of states against 2.76 GB. The sample is
estimated at 3.3 MB against 0.9 MB measured.

## Supported media

Gallery preview support is broader than the pinned training loader. GIF, TIF and TIFF
images remain browsable, but Job review names each incompatible included item and
requires conversion to PNG/JPEG/WebP/BMP or explicit exclusion before preparation.
Original media is preserved. The native dataset builder uses the same pinned media
extension lists as preflight, so supported PNG images remain image datasets.

## Default configuration

New Wan job and New H3 job start from a recipe that can produce a useful LoRA:

| Control                   | Wan     | H3      |
| ------------------------- | ------- | ------- |
| Size                      | 832×480 | 640×352 |
| Frames                    | 33      | 124     |
| Rank/alpha                | 16/16   | 8/8     |
| Training steps            | 600     | 600     |
| Save every N steps, state | 100, on | 100, on |
| Sample inference steps    | 20      | 10      |

Size, frames, rank and sample steps are the largest configurations measured in the
production trial (see [measured presets](#duplicates-and-presets)). 600 steps is the
owner's experience of where samples first change visibly, and is not a measured
learning result: the trial's human review of learning is still open. At the measured
step times 600 steps takes about 12–16 hours. The sample prompt starts empty, so
review asks for one.

Review warns when steps are below 600, because a shorter Run can check that training
works but is not expected to change samples. It also warns when H3 is larger than
640×352, the largest size that trained.

The earlier small recipe remains as the **Smoke test** built-in presets: 256×256, 17
frames for Wan or 124 for H3, batch one, rank/alpha eight for Wan or four for H3, two
steps saved every step, no state and a four-step sample with a synthetic prompt. These
values are also the fixed fallbacks for fields missing from saved revisions and
version 1 exports, so existing jobs keep their meaning. Acceptance fixtures use them
and stay quick.

Changing capacity controls requires capacity validation on Windows 11 / RTX 3090 /
96 GB RAM. Draft validation is not proof of successful GPU execution. Sampling runs
after training and can be disabled; no checkpoint-on-stop guarantee is made. Saving
optimizer/training state is distinct from saving LoRA weights.

## H3 jobs

New H3 job uses the proven T2VA guidance-loss recipe: 124 frames, BF16, 48 H2D-only swapped blocks, unconditional text caching and both video/audio
losses. Geometry must be a multiple of 32; the released frame lengths are `17*n+5`
between 124 and 345. Unsupported CFG, negative prompts, Ref2VA, FL2VA training and
full fine-tuning are not offered. H3 drafts require video items; Wan may use images
and videos, emitted as separate native dataset sections.

## Wan frame rates

Wan trains and samples at 16 fps. Each Wan video section sets `source_fps` to the
probed frame rate, so the pinned loader drops frames to 16 fps instead of training
a 30 fps clip as slow motion. `source_fps` applies to a whole section, so a dataset
with several frame rates is frozen into one `rate-N` folder, section and cache
directory per rate. Review names every resampled clip and its frame count before and
after resampling. A clip that is too short after resampling is blocked; Wan target
frames need `frames / 16` seconds of source video. Clips below 16 fps, or with an
unknown frame rate, are blocked because the loader cannot add frames; re-encode or
exclude them. H3 always resamples to 24 fps by frame timestamps and ignores
`source_fps`, so its TOML is unchanged. Wan latent and text caches prepared before
resampling are not reused.

## Frame extraction

Frame extraction chooses which windows of each clip are cached and trained. Head
(the default, and what earlier version 1 jobs keep) uses only each clip's first
window. Wan also offers chunk (consecutive non-overlapping windows), slide (a
window every stride, emitted as `frame_stride`) and uniform (the given number of
evenly spaced windows, emitted as `frame_sample`; at least two). The pinned `full`
mode is not offered because its variable clip length has no validated memory
budget. H3 uses head only. The pinned loader slices audio for every window, but the
GUI's audio alignment and coverage checks inspect only each clip's first window, so
other modes are blocked in review until those windows are inspected too. Review lists each video's
frame rate, frame count before and after resampling, and cached windows, plus the
total, so the cache size and epoch length are visible before preparation. Changing
the mode, stride or sample count changes the latent and text cache identities.
Counts are predicted from each clip's probed average frame rate and frame count;
variable-frame-rate or mislabelled clips can decode to a different count, so
re-encode them at a constant frame rate when the exact count matters.

## H3 audio

H3's audio policy defaults to requiring supervised audio for every included video.
Review shows item-specific source/alignment diagnostics and supervision totals.
Explicitly allowing missing audio produces an audio-less item excluded from audio
supervision; malformed supplied audio still blocks preparation. No original audio
is repaired or silently discarded. The selected audio VAE remains mandatory.

## Duplicates and presets

Duplicate job creates an independent identity and output destination. Save as preset
records named model-specific controls; Apply preset creates a new job revision while
retaining that job's name and dataset selection.

Two built-in **Measured** presets carry the largest configurations that trained on
the target PC during the production trial
([evidence](reference/evidence/production-trial.json)):

| Preset                                       | Size    | Frames | Rank/alpha | Memory                                           | Measured step time |
| -------------------------------------------- | ------- | ------ | ---------- | ------------------------------------------------ | ------------------ |
| Measured · Wan 832×480 · 33 frames · rank 16 | 832×480 | 33     | 16/16      | 36 swapped blocks, runtime FP8, checkpointing    | ~79 s at 280 W     |
| Measured · H3 640×352 · 124 frames · rank 8  | 640×352 | 124    | 8/8        | 48 of 50 blocks streamed H2D-only, checkpointing | ~71 s at 245 W     |

Applying a measured preset replaces only width, height, frames, batch size, rank,
alpha and the memory controls. Model components, prompt, steps, learning rate, seed
and checkpoint/state cadence keep the job's values, so set the step count yourself.
Saved presets still replace every setting. Applying a Smoke test preset also replaces
steps, checkpoint and state cadence, and the sample settings, and keeps components,
learning rate and seed. Built-in presets are part of the application, are never written
to the presets folder and do not change existing jobs. Step times depend on cooling and the GPU power limit: the same Wan job ran at
79–95 s/step on this PC as fan and power settings changed. H3 at 832×480 × 124 frames
ran out of VRAM at the first training step, so larger H3 sizes are not qualified.

## Archiving a job

Archive job hides a job you no longer need, even while it has Runs, because each Run's
frozen snapshot records the job's identity and revision. Nothing is
removed from disk, and version-one revision files are not rewritten: the archive is a
separate marker file in `archived-jobs` in the state directory. An older application
version ignores the marker and lists the job again.

An archived job leaves the job list and can't be edited, converted, used as a preset
source or prepared. Presets already saved from it stay, since they are independent
copies. Its Runs, outputs and saved states stay browsable from Runs. Those Runs also keep
working, because each uses its own frozen snapshot: you can still start a prepared Run,
retry a failed one or restore a saved state, even though that makes the archived job's
Run active again. Archived jobs lists these jobs with a Restore button, which brings a
job back unchanged.

A job can't be archived while it has an active Run: queued, preparing, training,
sampling or stopping, or still holding processes to reconcile. The refusal says why, and
the job stays in the list. Stop or cancel the Run first.

## Deleting a job

Delete job permanently removes a job and all its revisions, and works for listed and
archived jobs alike. It is refused while any Run of that job remains, whatever the Run's
state, including a Run with an unfinished delete. The refusal points to
[Delete run](runs.md#deleting-a-run) for each Run, or Archive job to hide the job instead.
Deleting a job and its Runs in one action was rejected, because it would remove far more
than the action suggests. Datasets, presets saved from the job and other jobs are left
alone. An interrupted delete leaves the job listed at its latest revision, to delete again.
Unknown job storage or an unknown Run database version blocks the delete.

## Model conversion

Model conversion first previews every changed model/task-specific field. Cancel
leaves the previous draft intact; Accept creates a revision while retaining the old
revision for comparison. Incompatible components and memory/recipe/frame defaults
reset, and shared controls remain.

## Import and export

Import / export configuration downloads or accepts strict `musubi-gui-job` version 1
JSON. Reimport creates an independent job. Unknown versions, formats and fields are
rejected; missing optional controls in earlier version 1 exports receive documented
family defaults. Recognised credential strings are redacted on export/import and in
presets. Review local filesystem paths before sharing a configuration. No model,
dataset or training-state payload is embedded in the export.

## Checks

Focused checks: `npm.cmd run typecheck` and
`.venv\Scripts\python.exe -m pytest tests/python/test_jobs_api.py -q`. Browser checks
cover the editor through the API and persistent storage. For manual review, save an
incomplete draft, restart, change frame count to 18 and enable inactive-DiT offload.
Review must explain both errors and disable Prepare run. Correct the fields, compare
the reviewed TOML and arguments with the controls, and verify reopening retains them.
Real preparation/training requires the compatible model files and environment described
in [reference workflows](reference/README.md); fake acceptance fixtures do not prove
model support.

Browser checks: `npm.cmd run test:browser -- tests/browser/jobs.spec.ts`. The scenarios
cover restart persistence and generated configuration for both models, frame
extraction with cached-window counts for a resampled 30 fps clip, invalid draft
feedback, duplication, presets, cancelled/accepted conversion and configuration
round-trip. These UI scenarios intentionally do not launch GPU work; the separate
release-acceptance fixtures exercise execution and real hardware evidence remains
separate.

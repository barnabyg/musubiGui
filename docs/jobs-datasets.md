# Datasets and media in a job

How a job uses its datasets and media. [Jobs](jobs.md) lists the other job topics.

## Datasets in a job

From an open dataset, choose **Create training job**, then **Create Wan job** or
**Create H3 job**. Pending captions save before a separate draft is created with
that dataset selected once. Failed caption saves keep the gallery open for repair.
Missing folders, scans that have not finished and datasets without valid included
media explain what to fix before creating a job. Existing jobs stay independent.

For H3, this handoff enables **Enable bucketing** and **Do not upscale buckets**
when an included video differs from the default training shape. This preserves
the portrait and landscape outputs of H3 video preparation. Existing saved jobs
retain their settings; Review warns if fixed-size training would crop videos of
a different shape. Portrait and mixed-orientation GPU training remain unmeasured
on the target PC, even though the two preparation buckets have equal pixel counts.

To check this handoff, run the following from `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

In the CPU fixture, edit a caption in WAN acceptance, create a Wan job from the
dataset, add H3 acceptance as a second dataset and set the first dataset's repeats.
Save and reload: both datasets retain their order and repeats, and reopening the
gallery shows the saved caption. Try the H3 handoff too. This checks navigation
and persistence without starting GPU training.

Job & dataset lists the job's datasets in order. Each row shows the dataset, its contents
(`12 images · 3 videos`), its repeats and Remove. Blank repeats use the job's Repeats, shown in
the field. Add a dataset offers only those not already listed. A row whose dataset was deleted
from the library says so and can be removed. Its review error focuses the row’s Remove
button. If a dataset has no valid included items, its review error opens the dataset.

Review's Training sections table has a row per section: dataset, type, items, repeats, samples
per epoch and share of training. A cached video window counts as one sample, as does an image,
and repeats multiply both. Review warns when one of several sections has over 80% of the samples,
because 200 images can otherwise swamp 10 clips without notice. This is only a warning.

## Training sections

### Per-dataset training resolution

Open **Dataset preparation** from the section menu or choose **Set training
resolutions in Dataset preparation** below the dataset list. **Default training
resolution** sets the shared width and height. Below it, each dataset shows its
**Effective training resolution**, marked **default** or **custom**. Leave **Use
default training resolution** checked to follow the shared default; uncheck it to
choose that dataset's width and height. Changing the default updates inherited
sizes and leaves custom sizes unchanged. Wan dimensions must be at least 64 and
multiples of 16; H3 uses multiples of 32. Bucketing remains a separate aspect-ratio
choice. Set sample and preview resolution separately in **Samples**; dataset
overrides never affect it.

Review lists the effective size of every training section. Overrides reach the native
dataset TOML, cache identity and latent disk estimates; changing one invalidates the
job's compatible preparation caches, including for a single-dataset job. Existing
jobs without overrides retain their geometry and cache keys. Revisions and exports
preserve overrides. Saved presets restore explicit sizes or inheritance for matching
dataset IDs without adding datasets; older presets and built-in presets leave dataset
overrides alone.

Musubi Tuner supports this through its dataset-level `resolution`, inheriting from
`[general]` when omitted; see the pinned
[configuration schema](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/dataset/config_utils.py#L113-L122).
Multiple training resolutions within one dataset are not supported by this UI.

From `C:/docs/git/musubiGui` in PowerShell 7, run:

```powershell
.\acceptance.cmd --workspace ".cache/resolution-check-$([guid]::NewGuid().ToString('N'))"
```

Open **WAN acceptance 1**, add **H3 acceptance** as its second dataset, then choose
**Set training resolutions in Dataset preparation**. Change the default width to
288: both datasets should show 288 × 256 marked **default**. Uncheck inheritance
for each and set the dataset training sizes to 320 × 256 and 256 × 320. Save and
reload: both sizes should remain, marked **custom**. Change the default: neither
custom size should change. In **Samples**, set sample width and height to 256.
Review should list both training sizes and pass, while the sample remains 256 × 256.
Choose **Prepare only** and expect a prepared Run. Repeat with an invalid width of
65: review should link to that dataset's width and block preparation. The default
fixture uses CPU workers; it proves orchestration and persistence, not GPU capacity
or useful training. Real GPU geometry changes still require capacity qualification.

`musubi_gui/training_sections.py` turns a job and its included items into native dataset
sections. Review and preparation both use it, so the TOML that review shows is the one that
runs, apart from the Run's own folder. A section has a dataset, a type (images or videos), a
frame rate, repeats, its media and a cache folder. A section sets `num_repeats` only when its
dataset row sets repeats; otherwise it inherits `[general]`.

A job with one dataset keeps its original layout, paths and cache keys, so its caches stay
reusable. A job with several uses these, where N is the dataset's position in the list and M
its frame rate's position within that dataset:

| Section               | Media                | Cache folder       |
| --------------------- | -------------------- | ------------------ |
| Images                | `media/set-N`        | `dataset-N-images` |
| Videos, one rate      | `media/set-N`        | `dataset-N`        |
| Videos, several rates | `media/set-N/rate-M` | `dataset-N-rate-M` |

Every section has its own cache folder, as upstream requires. The section layout and repeats
join the cache keys of these jobs, so caches are reused only between jobs with the same
datasets in the same order with the same repeats.

An H3 video section lists its clips in its own JSONL, `dataset.jsonl` for one dataset and
`dataset-N.jsonl` for several. Retry copies each of them. Frame-rate sections and window counts
work within each dataset.

With several datasets, review starts each item's errors and warnings with the dataset's name,
because relative paths can repeat across datasets. It also warns when two datasets hold
identical files (the same SHA-256), which train once for each dataset. It compares contents
only for files of the same size.

## Preparing images, videos, or both

Add one or more datasets under **Job & dataset**. For example, select a character's
image dataset, choose **Add dataset**, then add its video dataset. The source
folders stay separate and reusable. A folder containing both images and videos
also works. Dataset contents come from valid, included files; no media type needs
to be chosen when creating a dataset.

In the **Dataset preparation** tab, width, height, bucketing and
repeats apply to both media kinds. Images always train as single frames. An
image-only job hides frame extraction and H3 audio policy; saved video settings
are retained for when a video dataset is added again. **Frames** applies to video
training windows. **Samples** configures [sample resolution and duration](jobs-previews.md#sample-resolution-and-duration) separately. H3
samples still require 124–345 frames in the supported increments, even when
training on images. Turning both sampling and previews off removes that unused
length requirement. Images never need supervised audio. H3 image training keeps
its experimental warning described under [H3 images](#h3-images).

Review lists each image/video section, item count, repeats and share of training.
Set an image dataset's repeats to 2 to double its samples per epoch without
duplicating source files. Preparation freezes captions and media into the Run
and gives each section its own cache directory. Caption and media edits made later
reach a stopped Run when it resumes with a refreshed snapshot, which caches only the
changed items again; see
[Resuming with a refreshed dataset](runs-saved-state.md#resuming-with-a-refreshed-dataset).

From `C:/docs/git/musubiGui` in PowerShell 7, this single command installs the app
dependencies, builds and launches an isolated CPU workspace with image-only,
video-only and combined jobs for both model families:

```powershell
.\acceptance.cmd --workspace ".cache/dataset-media-$([guid]::NewGuid().ToString('N'))" --dataset-media
```

Open the printed URL (normally `http://127.0.0.1:8767`). In Jobs, compare **WAN
images only**, **WAN acceptance 1** (videos) and **WAN images + videos**. Open
each relevant tab and check the applicable controls. Review and **Prepare only**
each job. Expect one image section, one video section, or both respectively; the
combined job's images have repeats 2. Reopen the job and expect its dataset list
and repeats to remain. Repeat for H3, checking its experimental image warning
and video-only audio coverage. The fixture uses synthetic weights and CPU
subprocesses: it verifies preparation and persistence, not GPU support or useful
learning. Real training requires the configured pinned training environment,
model weights and suitable hardware.

## Supported media

Gallery preview support is broader than the pinned training loader. GIF, TIF and TIFF
images remain browsable, but Job review names each incompatible included item and
requires conversion to PNG/JPEG/WebP/BMP or explicit exclusion before preparation.
Original media is preserved. The native dataset builder uses the same pinned media
extension lists as preflight, so supported PNG images remain image datasets.

With bucketing off, every image is trained at the job's exact size: the pinned loader scales it
to cover that size and crops the overflow from the centre (`image_video_dataset.py`). Review
warns with the number of included images in each dataset that this reshapes, using the size the
scan recorded, and allows 1% of difference in shape. Enabling bucketing clears the warning, though
bucketing with H3 images has not been tried on the target PC, so its warning suggests cropping instead.

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
(the default, and what earlier jobs keep) uses only each clip's first
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

## H3 jobs

New H3 job uses the proven T2VA guidance-loss recipe: 124 frames, BF16, 48 H2D-only swapped blocks, unconditional text caching and both video/audio
losses. Geometry must be a multiple of 32; the released frame lengths are `17*n+5`
between 124 and 345. Unsupported CFG, negative prompts, Ref2VA, FL2VA training and
full fine-tuning are not offered. Images and videos are emitted as separate
[native dataset sections](#training-sections) for both models.

### H3 images

H3 can also train on images, with upstream's experimental one-frame mode. When a job includes
images, the latent cache, text cache and training gain `--one_frame`, and a job with only images
also gains `--video_only` on training, following upstream's table. The sample stays a video.
Video-only jobs keep the same commands and cache keys. Image sections use a plain
`image_directory` with their own cache folder, and only clips go in the JSONL.

Review warns that this is experimental and that using the result for video is unvalidated, and
[what was measured](reference/h3.md#image-and-mixed-trials-1-november-2026) proves only that it
runs. It also mentions upstream's advice to state the absence of sound in image captions. Captions
belong to the dataset, so editing them affects every other model using it, and the application
never changes them itself. Audio inspection and coverage apply to videos: an image is never
counted as missing audio.

## H3 audio

H3's audio policy defaults to requiring supervised audio for every included video.
Review shows item-specific source/alignment diagnostics and supervision totals.
Explicitly allowing missing audio produces an audio-less item excluded from audio
supervision; malformed supplied audio still blocks preparation. No original audio
is repaired or silently discarded. The selected audio VAE remains mandatory.

## Repairing reviewed dataset items

In Configuration review, follow an item error or warning to open its media preview,
caption editor or H3 audio inspector. The link identifies the dataset and item even
when several datasets contain the same filename. Captions, audio sources and exclusions
are shared by all jobs using that dataset. **Return to job and review** waits for
caption saves, bulk changes, uploads and rescans, reopens the originating job and checks its current inputs again. Warnings
allow preparation when all errors have been resolved.

`POST /api/jobs/{job_id}/review` returns the reviewed `job_id` and `revision`.
The `errors` and `warnings` arrays contain message strings. The optional target
for a message is indexed by its zero-based position in `error_targets` or
`warning_targets`. For example, `warning_targets["0"]` describes `warnings[0]`.
A field target has a `field` key. A dataset target has a `dataset_id` and can
include an `item_id` and a `view` of `caption`, `audio`, or `preview`.

When identical files occur in multiple datasets, the warning links each copy
to its own preview. The review API keeps the complete warning text and adds
`summary` and `items` to its `warning_targets` entry. Each item includes the
display `label`, `dataset_id` and `item_id`.

Repair links from an H3 job carry its reviewed frame count in the `frames`
query parameter. The audio inspector uses that window both when inspecting
the clip and when applying a new audio source. The item API accepts the same
optional parameter on `GET /api/datasets/{dataset_id}/items/{item_id}/h3`
and `PUT /api/datasets/{dataset_id}/items/{item_id}/audio`. Both use 124 frames
when the parameter is omitted.

To try this from the repository root on Windows with the acceptance prerequisites:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Create an H3 job using **Mixed media and invalid item**, review it, and use Enter on
the **motion.mp4** supervised-audio error. The H3 inspector opens for that clip.
Return to the job, choose the allow-missing audio policy and review again. The audio
policy message now appears as a warning. Open the missing-caption warning for
**blue.png**, enter a caption and return. That warning should disappear.

To check a longer H3 window in the same fixture, open **H3 acceptance 1**,
set **Frames** to 141 and review. Follow the `motion.mp4` short-audio error
and choose **Inspect H3 media**. The inspector should identify the 141-frame
window and report the audio shortfall. Choose **Apply audio source** with the
path blank to check the existing sidecar against the same window.

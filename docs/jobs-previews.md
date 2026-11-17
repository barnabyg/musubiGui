# Samples and previews

Sample and preview settings in the job editor. [Jobs](jobs.md) lists the other job topics.

## Sample memory

Standalone previews and final samples choose their GPU memory settings separately
from training. Samples offers **Automatic with reserved headroom** or the
**Conservative family preset**; review explains the choice. Native previews retain
training residency. See [preview memory selection](reference/preview-memory.md)
for measured profiles, conservative fallbacks, compatibility and an isolated
manual comparison command.

## Previews during training

Set **Preview every N steps** in Samples. Zero (the default for new and
existing jobs) disables previews. Exports store this as `config.preview_every`; older
files that omit it keep previews off. The interval counts optimizer updates after gradient
accumulation, per expert for alternating Wan, independently of checkpoint saving. Previews share the sample prompt,
seed, inference steps and geometry. The final sample checkbox is independent: previews
can run with final sampling off. Review requires a prompt for either choice.

For H3 and low-noise Wan, the pinned trainer pauses training for native inference in the same process, caches
sample text before loading the training model, keeps VAEs on CPU between previews,
and switches block swapping to inference and back. It restores Torch RNG state and
retains the optimizer, scheduler and current dataset traversal. It does not restart or
reshuffle training at each preview. Sampling and VAE decoding add time and GPU memory;
the 24 GB capacity beyond the small measured recipes still needs hardware qualification (#129).

H3 uses native joint audio/video sampling. Single-expert Wan previews use only the
trained low-noise expert throughout inference; they are diagnostic samples and differ
from the final two-expert video.
With Wan Frames set to 1, native periodic previews are still PNG images.

Alternating Wan previews use both experts at matching per-expert steps, independently of
the expert-switch interval. Training saves and exits at each preview boundary, releasing
its GPU process before the existing two-expert generator loads. The next phase restores
the expert's optimizer, scheduler and RNG state. **Every extra preview boundary restarts
the shuffled dataset pass**, just like an expert switch; it also adds model-loading time
and retains another checkpoint/state. Review shows the split phases and estimates their
disk use. The GPU lease remains held throughout, preventing another Run from overlapping.

An expert with no updates yet uses its base weights. Each preview records and displays
the low/high trained-step counts (zero for an untrained expert) and retains its own pair
identity even after later updates. Final sampling is still independent. On restore,
paired previews continue at absolute per-expert multiples rather than resetting the cadence.
Full paired-preview hardware qualification remains tracked in #129.

Completed previews appear automatically on the active Run within about five seconds
plus file inspection time. Run outputs labels their segment step and model scope; select
a preview there to play it. New arrivals preserve the selected preview and playback.
For native single-process previews, a restored segment continues the original Run's step
numbering. Frozen prompt inputs belong to the Run, and restoring a state keeps them.
[Resuming with an edited revision](runs-saved-state.md#resuming-with-an-edited-job-revision)
writes that revision's prompt into the new segment instead. Retry copies and seals that input
like the dataset configuration.

From `C:/docs/git/musubiGui`, in PowerShell 7, this command prepares and launches
an isolated 15-image, 500-step test with previews every 100 steps:

```powershell
.\acceptance.cmd --workspace ".cache/preview-images-$([guid]::NewGuid().ToString('N'))" --preview-images
```

Open WAN acceptance 1 or H3 acceptance 1 and choose Prepare and start. Expect previews
at 100, 200, 300, 400 and 500, continuing progress after each, then a final LoRA and sample.
Select the first preview while training continues and confirm it keeps playing as later
previews and the final sample appear. Try stopping a second Run; completed previews stay available. This
default mode uses synthetic CPU workers and proves orchestration only.

For a real GPU trial, add `--mode gpu --settings .state/settings.json` to that command.
Prerequisites are the pinned musubi-tuner installation, configured compatible models,
the target RTX 3090/96 GB host, and enough free disk for model caches and checkpoints.
The 15 generated images establish execution and memory use, not useful learning.
Record peak VRAM/RAM and preview overhead under #129; start with a short Run to establish
capacity before running all 500 steps. Installed training packages are left untouched.
Add `--wan-paired` to prepare the same 500-step Wan test with alternating experts,
switching every 30 updates and generating paired previews every 100 total updates.

The [GPU evidence](reference/evidence/in-run-previews.json) records completed 500-step
native H3 and low-noise Wan image trials and a short paired Wan trial. These small
recipes establish execution and measured capacity; larger settings and useful learning
remain unqualified.

## Sample resolution and duration

In **Samples → Sample and preview resolution & duration**, choose width, height
and duration in seconds.
Both periodic previews and final samples use these values. Training and dataset
geometry stay unchanged. Width/height must be at least 64, in multiples of 16 for
Wan or 32 for H3. Duration rounds to the nearest supported frame count: `4*n+1`
at 16 fps for Wan; `17*n+5`, from 124 to 345 frames, at 24 fps for H3. The effective
resolution, frames, fps and duration appear beside the controls and in Review;
out-of-range values are errors. For example, Wan 2 seconds resolves to 33 frames
(2.0625 seconds), and H3 6 seconds resolves to 141 frames (5.875 seconds).
Native single-expert Wan previews use the pinned trainer's fixed 24 fps. Explicit
durations resolve separately onto its frame grid: the 33-frame final sample above
uses a 49-frame preview (2.041667 seconds). Samples and Review show this distinction.
Paired Wan previews use 16 fps, matching final samples. Older Jobs without an
explicit duration retain their original preview frame count and playback rate.
Wan also offers **Image sample (one frame)**, producing PNGs. H3 sampling stays
video/audio even when its training dataset contains only images.

Jobs and exports without sample overrides retain their previous behaviour:
sample width, height and frames inherit the Job defaults. **Use default training width**,
**Use default training height** and **Use training frame count** clear individual overrides.
Versioned exports store explicit overrides as `config.sample_width`,
`config.sample_height` and `config.sample_frames`; frame counts are authoritative,
so saving/reopening does not accumulate duration rounding. Disk preflight uses
the effective sample geometry independently of the training cache estimate.

To test from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/sample-geometry-$([guid]::NewGuid().ToString('N'))"
```

Set a Wan sample to 320×192 and 2 seconds; enable a preview every step. Review
should show 33 frames at 16 fps, while training stays 256×256 with 17 frames.
Prepare and start, open Samples and play the generated media. Repeat with H3 at
320×192 and 6 seconds: expect 141 frames at 24 fps while training stays at 124.
This CPU fixture verifies configuration, orchestration and decoded metadata, not
GPU capacity or learning quality.

The [short GPU sample checks](reference/evidence/run-screen-samples.json) on the
RTX 3090 decoded a 320×192 Wan final sample at 33 frames/16 fps, its corrected
native preview at 49 frames/24 fps, and H3 preview/final samples at 141 frames/24 fps
with audio. Training remained 256×256 with 17 Wan or 124 H3 frames. These synthetic
two-step checks establish execution and metadata only; production capacity,
learning and audible quality are unqualified.

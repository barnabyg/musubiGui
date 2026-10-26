# Job drafts and configuration review

Launch with `launch.cmd`, register a dataset, and open Jobs. New Wan job creates an
incomplete saveable draft. Select its dataset and edit the grouped preparation,
LoRA, memory, checkpoint and sample controls. Save draft creates a new immutable
revision; reopening the application retains every supported value. An outdated tab
cannot overwrite a newer revision.
Revision allocation is serialized across backend processes sharing the same state
directory, including saved drafts, confirmed model conversions and preset application.
The version-one revision JSON files remain unchanged.

Review configuration shows effective included videos, resolved component/output
paths, native dataset TOML and each upstream argument array. Errors block preparation
while the draft remains editable. Component overrides must stay inside the configured
model roots. The initial Wan recipe trains the low-noise expert; high-noise-only and
combined training are not qualified. Runtime FP8 scaling requires native FP16 sources,
not saved scaled-FP8 weights. Inactive-DiT offload is not supported by this recipe.

The small default is 256×256, 17 frames, batch one, rank/alpha eight and two steps.
Changing capacity controls requires capacity validation on Windows 11 / RTX 3090 /
96 GB RAM. Draft validation is not proof of successful GPU execution. Sampling runs
after training and can be disabled; no checkpoint-on-stop guarantee is made. Saving
optimizer/training state is distinct from saving LoRA weights.

Focused checks: `npm.cmd run typecheck` and
`.venv\Scripts\python.exe -m pytest tests/python/test_jobs_api.py -q`. Browser checks
cover the editor through the API and persistent storage. For manual review, save an
incomplete draft, restart, change frame count to 18 and enable inactive-DiT offload.
Review must explain both errors and disable Prepare run. Correct the fields, compare
the reviewed TOML and arguments with the controls, and verify reopening retains them.
Real preparation/training requires the compatible model files and environment described
in [reference workflows](reference/README.md); fake acceptance fixtures do not prove
model support.

New H3 job uses the proven T2VA guidance-loss recipe: 124 frames, rank/alpha four,
BF16, 48 H2D-only swapped blocks, unconditional text caching and both video/audio
losses. Geometry must be a multiple of 32; the released frame lengths are `17*n+5`
between 124 and 345. Unsupported CFG, negative prompts, Ref2VA, FL2VA training and
full fine-tuning are not offered. H3 drafts require video items; Wan may use images
and videos, emitted as separate native dataset sections.

H3's audio policy defaults to requiring supervised audio for every included video.
Review shows item-specific source/alignment diagnostics and supervision totals.
Explicitly allowing missing audio produces an audio-less item excluded from audio
supervision; malformed supplied audio still blocks preparation. No original audio
is repaired or silently discarded. The selected audio VAE remains mandatory.

Duplicate job creates an independent identity and output destination. Save as preset
records named model-specific controls; Apply preset creates a new job revision while
retaining that job's name and dataset selection. Model conversion first previews every
changed model/task-specific field. Cancel leaves the previous draft intact; Accept
creates a revision while retaining the old revision for comparison. Incompatible
components and memory/recipe/frame defaults reset, and shared controls remain.

Import / export configuration downloads or accepts strict `musubi-gui-job` version 1
JSON. Reimport creates an independent job. Unknown versions, formats and fields are
rejected; missing optional controls in earlier version 1 exports receive documented
family defaults. Recognised credential strings are redacted on export/import and in
presets. Review local filesystem paths before sharing a configuration. No model,
dataset or training-state payload is embedded in the export.

Browser checks: `npm.cmd run test:browser -- tests/browser/jobs.spec.ts`. The scenarios
cover restart persistence and generated configuration for both models, invalid draft
feedback, duplication, presets, cancelled/accepted conversion and configuration
round-trip. These UI scenarios intentionally do not launch GPU work; the separate
release-acceptance fixtures exercise execution and real hardware evidence remains
separate.

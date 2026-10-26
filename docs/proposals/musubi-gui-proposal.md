# Musubi GUI implementation proposal

Draft for discussion, 26 October 2026.

Build a standalone browser application around musubi-tuner, with the familiar dataset gallery and grouped job editor from AI Toolkit as the interaction reference. Target native Windows 11 with an RTX 3090, LoRA training only, Wan 2.2 T2V, and MiniMax H3 T2VA with video/audio. Prioritise dataset management and job creation/editing, then connect those screens to a supervised training pipeline.

The user's concern is AI Toolkit's underlying training reliability, particularly MiniMax H3, rather than its GUI. Preserve the interaction style and use musubi-tuner's training implementation, with configuration validation and reproducible execution around it.

The repository currently contains agent guidance but no application implementation. The architecture and milestones below are proposed choices. The upstream requirements are based on the linked documentation as inspected on this date; implementation must pin a tested musubi-tuner revision.

## Product scope

The first release should let a user import an existing dataset, review and edit its captions and conditioning media, create or duplicate a training job, understand validation failures, prepare caches, run training, and find its checkpoints and samples. Closing the browser must not stop training.

Use a persistent sidebar with Datasets, Jobs, and Settings. Keep AI Toolkit's dense, grouped controls and media-first browsing, with visible save states and validation alongside the affected field. Use both open-source repositories directly for guidance: AI Toolkit for interface patterns and musubi-tuner for training integration. Reuse suitable code where useful, preserving applicable licences and attribution.

AI Toolkit already provides a virtualised dataset gallery, a media viewer with caption editing, and grouped model-dependent job controls. These are useful interaction references, rather than evidence that its training configuration can be reused for musubi-tuner. [Dataset screen](https://github.com/ostris/ai-toolkit/blob/main/ui/src/app/datasets/%5BdatasetName%5D/page.tsx), [media viewer](https://github.com/ostris/ai-toolkit/blob/main/ui/src/components/DatasetImageViewer.tsx), [job editor](https://github.com/ostris/ai-toolkit/blob/main/ui/src/app/jobs/new/SimpleJob.tsx).

## Dataset management

Provide a dataset library showing name, location, media counts, caption coverage, and validation status. Support registering folders already on the training machine and uploading into an application-managed dataset. Clearly distinguish server paths from files selected on the browser's machine.

The dataset detail screen should include:

- A virtualised image/video gallery, search, sorting, selection, and filters for missing captions, invalid media, and conditioning errors.
- A large preview with video playback, audio playback where relevant, metadata, caption editing, and keyboard navigation.
- Explicit saving, saved, and failed states. A failed save must retain the edit; external file changes must produce a conflict rather than silently overwrite data.
- Bulk caption prefix/suffix and find/replace with a preview of affected items. Preserve recoverable caption history. Excluding an item from training should not delete its source file.
- Dataset preparation settings for resolution, bucketing, repeats, frame extraction, and caching, separated from the original media.
- Audio inspection for H3 T2VA: show whether audio comes from an explicit file, a same-stem sidecar, or the embedded video track, and report alignment or missing-audio problems. First/last-frame and ordered reference editors are deferred with their training modes.
- A validation report that names each failing item and gives an actionable correction. Display the number of excluded or skipped items before training starts.

Keep registered source media in place and write caption edits directly to the corresponding `.txt` sidecars as edits are made, without a separate export step. Use short debounced autosaves, flush pending edits on navigation, and show saved only after the backend confirms a successful atomic file replacement. Retain unsaved text on failure, detect external modifications, and preserve recoverable caption history. Bulk edits write to the same sidecars after their preview is accepted. Treat sidecar text as the authoritative caption; run snapshots are separate and must not delay these writes. Generate musubi-compatible dataset configuration and metadata at the preparation boundary, preserving unsupported imported fields where possible and reporting fields that cannot be represented.

Automated captioning is not required and is outside the initial scope. Prioritise manual caption editing and reliable preservation. Destructive cropping, a full video editor, deduplication by visual similarity, and dataset hosting are also outside the initial scope.

## Job creation and editing

Use one grouped editor with a section navigator, rather than a long sequence of mandatory wizard steps. Suggested sections are Model and task, Datasets, Training, Memory and performance, Checkpoints and samples, and Review.

Support saveable drafts, duplication, named presets, inline help, and advanced controls. The model and task determine the available fields and validation rules. Switching a model must explain incompatible settings and preserve the previous draft until the user accepts the conversion.

The review panel should show the resolved dataset, model files, output location, required preparation work, warnings, and generated configuration/command. Offer configuration export for troubleshooting. Import should initially support the configurations the application generates; broader handwritten-config support needs an explicit compatibility matrix.

Separate a Job, which is an editable definition, from a Run, which records one execution of an immutable configuration revision. Editing a job must not change an active run. Store configuration, dataset manifest, model identities, upstream revision, command arguments, logs, outputs, and cache provenance for each run.

Provide Prepare, Start, Stop, and Resume when supported. Distinguish resuming saved optimizer/training state from starting a new run using LoRA weights. Describe stop behaviour accurately; offer force termination separately, and do not promise a checkpoint on stop unless the adapter can guarantee it.

## Model support and upstream constraints

| Family | Proposed first supported workflow | Required distinctions |
| --- | --- | --- |
| Wan 2.2 | A14B T2V LoRA | Low-noise and high-noise experts, model paths, timestep selection, memory options |
| MiniMax H3 | T2VA LoRA with video/audio | Audio policy, geometry, weight formats, recipe-specific preparation |
| Later architectures | Added through the same adapter contract | Their own validated tasks, presets, configuration generation, and preparation stages |

Wan's documentation describes separate low/high expert training and simultaneous training. Treat these as explicit strategies; do not hide them behind a generic Wan toggle. Choose a validated default after testing on the 3090. A14B uses the Wan 2.1 VAE and T5; reject a mismatched 5B VAE. Validate incompatible memory options such as inactive-DiT offloading combined with block swapping. I2V and TI2V 5B are outside the first release. [Wan documentation](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/wan.md).

MiniMax H3 supports T2VA, FL2VA, and Ref2VA LoRA workflows, with experimental one-frame workflows documented separately. Its video contract includes 24 fps, dimensions divisible by 32, and frame counts of `17*n+5`, with a documented released range of 124–345 frames. Audio targets are optional, but the audio VAE is a required component. Reference ordering, audio alignment, supported weight formats, and recipe-specific cache tasks need dedicated validation. These rules must come from the pinned adapter version rather than being applied to every video model. [H3 guide](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/minimax_h3.md), [one-frame guide](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/minimax_h3_1f.md).

For H3 T2VA, select and prove a supported recipe: training adapter, guidance loss, or teacher matching. Plain flow matching alone is not a supported recipe for the released distilled model. Adapter merging requires BF16 source weights; teacher recipes can require different tasks in different cache stages. Dataset batch size is one, with gradient accumulation controlling effective batch size. Hide unsupported CFG and negative-prompt controls. These distinctions matter even though only T2VA is in scope. [H3 training recipes](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/minimax_h3.md).

Musubi-tuner uses dataset TOML and supports metadata JSONL and sidecar captions. The GUI should generate those formats without inventing a replacement training format. Its own job definitions should be versioned so existing saved jobs remain readable after upgrades. [Dataset configuration](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/dataset_config.md).

## Technical approach

Use React and TypeScript for the browser UI, a Python FastAPI service for validation and filesystem access, and a separate Python worker supervising musubi-tuner subprocesses. Use SQLite for datasets, job revisions, run state, and preparation tasks; keep large media, caches, logs, and checkpoints on disk. This keeps Python training integration direct while allowing a rich browser interface. Confirm this stack during the initial design milestone.

The backend should expose architecture adapters that own capabilities, defaults, validation, configuration generation, preparation stages, command arguments, output discovery, and resumability. Share dataset and run infrastructure while keeping model-specific rules inside adapters. Two working adapters should establish this seam before building a general plugin framework.

Use an existing musubi-tuner installation, launching its CLI with its selected Python environment and argument arrays. Setup should let the user select the checkout, Python executable, model files, and output/cache locations, then validate their compatibility. Record the exact upstream revision and environment with each run and define a tested compatibility range. Do not silently upgrade or modify the selected installation. Keep upstream upgrades deliberate, with compatibility checks; the upstream README explicitly recommends releases as stable integration reference points. [Musubi-tuner README](https://github.com/kohya-ss/musubi-tuner#releases).

Persist a pipeline of validation, latent caching, text caching, training, and output indexing. Stages may vary by adapter. Fingerprint caches using relevant media/caption content, preparation settings, model components, and upstream compatibility version. A caption change should invalidate the affected text caches; conditioning changes can also affect latent caches. Never rely only on a cache file's existence.

The worker owns process lifecycle and a durable single-GPU queue. Track subprocess identity and reconcile state after a service restart; a vanished worker must not cause a second training process to start over an existing one. Mark uncertain runs interrupted and require reconciliation before restart. Stream logs and progress to the browser; show unknown progress honestly when parsing is unavailable.

Freeze captions and metadata for a run, and validate referenced source content before preparation. If immutable source media cannot be guaranteed, offer managed copies or detect changes and require a new run revision. Avoid claiming reproducibility from a manifest alone.

Target native Windows 11 with one user, one training host, and one GPU job at a time. Serve the UI locally in the browser and provide a Windows launcher. Test Windows process termination, file locking, path handling, and installation explicitly. Bind locally by default. Remote browser access requires authentication and an agreed network setup. Restrict filesystem operations to configured roots, protect original files, keep credentials out of exports/logs, and account for cache disk usage during preflight. Remote hosting and multi-host orchestration are deferred.

The target PC has an RTX 3090 with a 24 GB VRAM budget, 64 GB of system RAM, and more than 400 GB of available disk space. Validate each stage independently, including H3 text caching and sample generation: a training configuration fitting in memory does not prove that preparation or sampling will fit. Measure RAM, VRAM, disk use, and runtime before selecting quantization/offload presets. Preflight must account for model files, caches, temporary files, checkpoints, and retained outputs rather than assuming the available disk space guarantees capacity. If H3 T2VA cannot complete on this hardware, report the precise blocker and configuration attempts before proposing a different environment or reduced scope.

## Delivery sequence

Each row is a proposed implementation ticket group, to split into independently verifiable tickets. No GitHub issues have been created.

| Milestone | Deliverable | Completion gate |
| --- | --- | --- |
| 1. Reference workflows on Windows | Pin upstream; prove Wan 2.2 T2V and H3 T2VA LoRA preparation, training, and sample generation on the RTX 3090 | A short run for each model produces a checkpoint and playable sample; RAM/VRAM use and any limitations are recorded |
| 2. Screen prototype | Dataset library/detail and grouped job editor using realistic image/video fixtures | User can review captions and create/duplicate/edit a model-specific draft; layout and workflow agreed |
| 3. Dataset implementation | Import, indexing, previews, immediate `.txt` caption saves, bulk actions, validation, and dataset export | A UI edit updates its source sidecar, survives restart, and is used in preparation; failed writes retain edits and caption history remains recoverable |
| 4. Job implementation | Versioned definitions, presets, model adapters, preflight, and generated-config review | Both selected model workflows produce valid configuration; invalid combinations are blocked with useful messages |
| 5. Wan execution | Worker, preparation stages, queue, logs, checkpoints, and supported stop/resume | User completes the dataset-to-checkpoint workflow through the GUI on target hardware |
| 6. H3 execution | Chosen T2VA recipe, audio preparation and validation | Same complete workflow passes with representative H3 video/audio, including missing/invalid audio cases |
| 7. Installation and reliability | Launcher, dependency diagnostics, recovery, upgrade compatibility, and documentation | Clean-checkout installation and launch work on each supported platform; browser closure and worker interruption behave as documented |

Prove both model families early in milestone 1 even if Wan execution ships first. This prevents a polished generic UI from concealing an H3 hardware or training limitation. Calendar estimates should follow the two hardware trials; the largest remaining variable is the H3 recipe's resource requirements on the target PC.

## Verification and manual acceptance

Before executable implementation, read the repository's verification guidance and establish the canonical checks if the referenced file is absent. Test configuration generation, adapter validation, cache invalidation, dataset compatibility, and process recovery. Browser tests must cover edits travelling through the API to the actual `.txt` sidecars, including failed writes and external-edit conflicts, and jobs reaching the worker with the reviewed configuration.

Use deterministic fake training processes for ordinary CI and real short GPU training runs for release acceptance. A fake worker passing does not establish model support. Include invalid media, missing captions, Unicode/spaced paths, stale caches, full disks, failed saves, and interrupted runs in focused regression coverage.

Every implementation ticket that needs manual verification must provide one documented command that prepares and launches its test scenario. That launcher does not exist yet. It should prepare a small fixture dataset and open the UI; GPU scenarios must list required model files and hardware explicitly.

The main acceptance scenario is: import a fixture dataset, edit a caption, verify the corresponding `.txt` file updates without export, refresh and verify it persists, create a job using that dataset, resolve a deliberately invalid setting, prepare and train, close/reopen the browser, and locate the checkpoint. Repeat for the selected Wan and H3 workflows. Resume acceptance must additionally show restored training state where supported.

## Confirmed decisions and setup details

The user confirmed Windows 11, RTX 3090, 64 GB RAM, more than 400 GB available disk space, LoRA only, Wan 2.2 T2V, and MiniMax H3 T2VA. Caption edits must update `.txt` files immediately. Automated captioning is not required. An existing musubi-tuner installation is acceptable. Both upstream repositories may be used directly for implementation guidance.

Use a single-user local browser app, an explicitly selected musubi-tuner installation, manual caption editing with immediate sidecar saves, and no automatic migration from AI Toolkit configs. The initial setup screen should discover or request installation/model paths and validate the selected environment, explaining missing dependencies. Those paths are implementation setup details, not blockers to the proposal. A fully managed installer can follow once the supported Windows environment is proven. No further product clarification is needed to turn this proposal into implementation tickets.

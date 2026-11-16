# Training settings

Training controls in the job editor, the new-job recipe and presets. [Jobs](jobs.md) lists
the other job topics.

## Timestep strategy

The LoRA & training tab offers **Shift**, **Sigmoid** and **Uniform**
for Wan. Shift favours noisier timesteps using a shifted sigmoid distribution;
Sigmoid concentrates draws around the middle; Uniform assigns equal probability
across the active expert's range. Sigmoid scale controls spread for Shift and
Sigmoid, while Flow shift controls the shift for Shift only. Both parameters must
be between 0.001 and 1000, avoiding unstable extremes in the trainer's FP32 draw.

Old jobs and new-job defaults keep the original Shift recipe: flow shift 3,
sigmoid scale 1 and distribution-preserving rejection sampling within each expert's
range. Alternative strategies and custom parameter values use the upstream bounded
mapping into each expert's range. This is deliberate: upstream rejection sampling
falls back to an unrestricted draw after 1000 misses, which can violate the expert
range for extreme custom distributions. Low/high phases keep ranges 0–875 and
875–1000 respectively. Review shows the effective strategy, parameters and mapping.

H3 displays a fixed Uniform strategy because the pinned trainer rejects other
strategies. It draws one base sigma shared by video/audio and applies their own
model-specific shifts. A saved unsupported H3 choice blocks review and can be
repaired by selecting Uniform. Switching model family applies the target family's
strategy defaults. Presets, revisions and exports preserve choices, and restoration
keeps the original Run's strategy regardless of later Job edits.

These controls follow the pinned upstream
[parser](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/training/parser_common.py#L444-L474),
[sampling and range mapping](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/training/trainer_base.py#L635-L760)
and [H3 restrictions](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/minimax_h3_train_network.py#L707-L720).
Short target-PC GPU checks exercised the original Shift recipe and Sigmoid with
scale 1.5 in alternating Wan training, plus H3's Uniform strategy, including state
restoration. See the [execution evidence](reference/evidence/training-settings-gpu.json).
Other custom parameter values and Wan Uniform have command/orchestration coverage.
Production-size capacity and training quality remain unqualified.

From `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/timesteps-$([guid]::NewGuid().ToString('N'))" --wan-paired
```

Open WAN acceptance 1, open LoRA & training, select Sigmoid and set its scale
to 1.5. Save and reload: the choice should remain. Review should pass and show
Sigmoid with scale 1.5 and bounded mapping; Advanced command arrays should keep
the appropriate low/high ranges. Prepare and start the four-step fixture, then
restore a complete save: the segment should keep its strategy. H3 acceptance 1
should show Uniform with an explanation and no incompatible choices. This is a
CPU orchestration test; no models or GPU are required.

## Optimizer selection

The LoRA & training tab offers AdamW and, when the selected training
Python can load a CUDA-enabled bitsandbytes optimizer, AdamW8bit. Both Wan and H3
use the pinned trainer's shared optimizer implementation. AdamW remains the
default for old and new jobs. A saved or imported unavailable choice stays visible
but blocks review until the environment is compatible or AdamW is selected.
Saving presets, revisions and exports preserves the optimizer. Review shows its
effective value, and restoring a Run keeps that Run's original optimizer even if
the Job has since changed. A conflicting frozen optimizer/command is rejected.

The supported subset comes from the pinned upstream
[optimizer implementation](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/training/trainer_base.py#L225-L307).
The target RTX 3090 Windows environment has bitsandbytes 0.50.1 with a CUDA-enabled
extension. Both optimizers completed preparation, training, checkpoint saving and
restoration for Wan and H3 on two synthetic images at 256 × 256. Wan trained both
experts for one update each, then restored for another update each; H3 trained two
updates and restored for one more, with its saved optimizer counter reaching three.
The [GPU evidence](reference/evidence/training-settings-gpu.json) records the
configuration, commands, package/model identities, checkpoint hashes and restored
counters. Final sampling was off. These checks establish execution for the recorded
settings, not production capacity or useful learning.

From `C:/docs/git/musubiGui` in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/optimizer-$([guid]::NewGuid().ToString('N'))"
```

Open WAN acceptance 1, open LoRA & training, select AdamW8bit, save and reload.
The selection should remain; Review configuration should show Optimizer: AdamW8bit.
Repeat with H3 acceptance 1. The fixture explicitly labels optimizer execution as
simulated. No model weights or GPU are required; real optimizer execution needs
the existing pinned training environment, compatible model paths and CUDA hardware.

## Default configuration

New Wan job and New H3 job start from a recipe that can produce a useful LoRA:

| Control                           | Wan                  | H3            |
| --------------------------------- | -------------------- | ------------- |
| Training recipe                   | Alternating high/low | Guidance loss |
| Steps per expert before switching | 30                   | —             |
| Size                              | 832×480              | 640×352       |
| Frames                            | 33                   | 124           |
| Rank/alpha                        | 16/16                | 8/8           |
| Training steps                    | 600                  | 600           |
| Save every N steps, state         | 100, on              | 100, on       |
| Sample inference steps            | 20                   | 10            |

Size, frames, rank and sample steps are the largest configurations measured in the
production trial (see [measured presets](#duplicates-and-presets)). 600 steps is the
owner's experience of where samples first change visibly, and is not a measured
learning result: the trial's human review of learning is still open. At the measured
step times 600 total optimizer updates takes about 12–16 hours. The sample prompt starts empty, so
review asks for one.

Review warns when steps are below 600, because a shorter Run can check that training
works but is not expected to change samples. It also warns when H3 is larger than
640×352, the largest size that trained.

The **Smoke test** built-in presets use 256×256, 17 frames for Wan or 124 for H3,
batch one, rank/alpha eight for Wan or four for H3 and a four-step sample with a
synthetic prompt. Wan alternates experts every step for four steps per expert, with
optimizer state saving enabled. H3 uses two steps without optimizer state saving.
Both save a checkpoint every step. The internal single-expert acceptance fixtures
remain available for isolated orchestration checks; they are not a job creation option.

Changing capacity controls requires capacity validation on the
[target hardware](../README.md#target-hardware). Draft validation is not proof of successful GPU execution. Sampling runs
after training by default, with opt-in [in-run previews](jobs-previews.md#previews-during-training), and can be disabled;
no checkpoint-on-stop guarantee is made. Saving
optimizer/training state is distinct from saving LoRA weights.

## Duplicates and presets

Duplicate job creates an independent identity and output destination. Save as preset
records named model-specific controls; Apply preset creates a new job revision while
retaining that job's name and datasets.

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

## Caching

The Dataset preparation tab offers separate latent and text
embedding cache policies. **Reuse compatible cache** is the default; preparation
copies verified compatible artifacts into the new Run's private cache, or builds
them when none are usable. **Rebuild cache** always runs that encoder for the new
Run. Both stages are required by the Wan/H3 recipes. Other Runs' caches remain
untouched. Review shows the policies; the Run shows each stage's actual result.
Saved drafts, presets and exports retain the choices. Retry retains its frozen
choices; Restore continues from the frozen prepared inputs without recaching.
See [the pinned upstream investigation and manual test](reference/caching.md).

## Alternating Wan high/low LoRAs

The **Smoke test · Wan alternating · 4 steps** preset selects a complete small paired
configuration with N = 1 and state saving enabled. It is the only Wan smoke preset.

Choose **Wan alternating low/high experts** under Recipe. **Steps per expert before
switching** is a positive integer N, counted in optimizer updates after gradient
accumulation. **Training steps** applies to each expert: 1,000 steps trains low to
1,000 and high to 1,000, for 2,000 optimizer updates. A budget shorter than N still
trains both experts. Phases end early at checkpoint and preview boundaries so the
experts meet at the same trained step. Saving every 100 steps produces both low
and high checkpoints at 100, 200, 300, and so on, plus a final pair when needed.
Phase transitions retain additional recovery checkpoints. Review shows the exact
allocation and commands before preparation. Recorded Runs retain their frozen
plans. Explicit legacy configurations with wan_steps_per_expert set to false
retain the shared budget.
An alternating Run is limited to 10,000 phases to bound its stored command plan.

Optimizer/training state saving is required. Every phase has a distinct checkpoint
and saved-state name; an expert resumes its own previous weights, optimizer, scheduler
and RNG when it becomes active again. Each phase launches the pinned trainer again,
so small N adds substantial model-loading overhead and retains more phase checkpoints
and states. Disk estimates include every phase's final saves and interval saves.
The existing AdamW/constant-scheduler recipe is retained.

**Musubi starts a fresh shuffled dataset pass at every switch.** Its resume implementation
does not restore the batch position or epoch, and resets its displayed step counter.
The GUI maintains total and per-expert counters separately. A short phase can repeat
some examples before others are seen; this is not equivalent to an uninterrupted
dataset traversal.

Final sampling loads the trained low and high LoRAs into their respective experts,
with both checkpoint identities and step counts recorded. Alternating Runs also support
[periodic paired previews](jobs-previews.md#previews-during-training).

From the repository root in PowerShell 7, this command prepares and launches an isolated
four-steps-per-expert CPU demonstration at `http://127.0.0.1:8767`:

```powershell
.\acceptance.cmd --workspace ".cache/wan-pair-$([guid]::NewGuid().ToString('N'))" --wan-paired
```

To check checkpoint alignment, open **WAN acceptance 1** and set Training steps to 7,
Steps per expert before switching to 4, Save every N steps to 3, and Preview every N
steps to 3. Review must show seven steps for each expert. Choose **Prepare and start**;
expect matching saved pairs at 3, 6 and 7, previews at 3 and 6 with equal low/high
counts, and final progress 7/7. This CPU fixture needs no models or GPU.

For the unchanged four-step preset, open **WAN acceptance 1**, review the schedule,
and choose **Prepare and start**.
Expect four steps per expert, eight optimizer updates, a final LoRA for each expert under Checkpoints,
and a playable synthetic video. Close/reopen the browser and inspect the saved paired
states. Restore a matching pair with one additional step; both experts advance by one.
An interrupted unmatched pair first catches its lagging expert up. To exercise
stopping, edit a new paired job to 24 steps
with N = 6, start it, and stop during training. Inspect states and restore a complete
save; expect the remaining part of that phase before the next expert.

For real GPU verification, add `--mode gpu --settings .state/settings.json` to the
same command. Prerequisites are the installed pinned musubi-tuner/Python environment,
native Wan FP16 low/high experts, Wan 2.1 VAE and T5 paths configured in that settings
file, and the target RTX 3090/96 GB PC. Acceptance copies settings into an isolated
workspace and does not change the installation. CPU fixtures establish orchestration,
not real training or useful learning; see the [Wan reference](reference/wan.md).

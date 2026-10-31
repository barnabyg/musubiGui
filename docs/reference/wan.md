# Wan 2.2 A14B T2V LoRA

The initial validated smoke recipe trains the low-noise expert alone. It uses rank/alpha 8, batch one,
256×256, 17 frames at 16 fps, two optimizer steps, FP16 training, FP8 conversion from native
FP16 source, 36 swapped blocks, gradient checkpointing, AdamW and SDPA.
`wan.py` is the reusable command/config generator; each run saves resolved arguments
and dataset TOML. The expected final outputs are `checkpoints/wan-reference.safetensors`
and an MP4 under `samples/` in the unique configured output directory.

Required Settings components:

| Key        | Compatible input                                                        |
| ---------- | ----------------------------------------------------------------------- |
| `wan_low`  | Native Wan 2.2 T2V low-noise 14B FP16 safetensors                       |
| `wan_high` | Native Wan 2.2 T2V high-noise 14B FP16 safetensors; needed for sampling |
| `wan_vae`  | Native 16-channel Wan **2.1** VAE, not Wan 2.2 5B VAE                   |
| `wan_t5`   | Native Wan UMT5 encoder, not unconverted Transformers shards            |

The runner rejects saved scaled-FP8 DiTs, native-key mismatches and safetensors VAEs with
the wrong latent channels. `.pth` inputs are delegated to the pinned upstream's native loaders.
The original local Wan experts use scaled FP8 and cannot be passed to this recipe. Loader
`--fp8_scaled` converts a supported source at runtime; it does not make saved scaled FP8 valid.
No conversion or overwrite of the user's existing files is performed. The fixed FP16 recipe
rejects BF16 DiTs before training; those require matching mixed precision and separate validation.

Actual preflight on 26 October 2026 recorded the existing revision/environment in
[wan-preflight.json](evidence/wan-preflight.json). The existing scaled FP8 experts and
Transformers-key T5 were rejected. At that point no Wan cache, training, checkpoint or sample stage ran;
support was **unresolved**, with no conclusion about hardware fit. Attempts to retrieve
native replacements produced only partial transfers, except for the small native Wan 2.1 VAE.
Downloads were stopped when the user elected to supply replacement files and continue development.

## Offline workaround and actual target-PC result

The existing scaled-FP8 experts and text encoder were safely dequantized into
**separate** FP16 native safetensors files. This recovers their scaled numerical
values, not precision already lost to quantization. T5 keys were mapped to the
pinned native architecture. The full DiT/T5 key and shape sets matched upstream
meta-device models (1,095 and 242 tensors respectively); independent PyTorch
sample comparisons were exact. Original files and the training installation were
preserved, and no replacement weights were downloaded. See the reusable
[conversion commands and limitations](wan-conversion.md) and
[conversion evidence](evidence/wan-conversion.json).

The complete reference scenario then passed on Windows 11, RTX 3090 and the
existing nominal **96 GB** machine (95.6 GiB usable RAM), using the previously
recorded Python 3.12.10 / PyTorch 2.13.0+cu130 / CUDA 13.0 environment and pinned
upstream revision. Every stage exited zero. See
[wan-success.json](evidence/wan-success.json) and
[Edge playback evidence](evidence/wan-playback.json).

| Stage         | Seconds | Peak process-tree RSS (GiB) | Peak device VRAM (GiB) |
| ------------- | ------: | --------------------------: | ---------------------: |
| Fixture       |   2.234 |                       0.031 |                   0.85 |
| Latent cache  |  12.766 |                        1.64 |                   2.35 |
| Text cache    |  22.203 |                       11.59 |                   8.38 |
| Training      |  48.657 |                       17.05 |                   6.34 |
| Generation    |  95.453 |                       18.80 |                   8.38 |
| Sample decode |   0.578 |                       0.006 |                   0.84 |

The final checkpoint was 153,492,416 bytes. The 258,973-byte sample decoded 17
video frames. Edge played it at 256×256 for 1.0625 seconds, advanced its playback
time and decoded frames without a media error. The sampled stage disk deltas
totalled approximately 2.02 GB, including unrelated activity; this excludes
68.52 GB of newly derived weights and the existing quantized sources. These
results establish short-scenario execution with these particular lossy-derived
weights. They do not establish visual quality, useful learning or production
capacity. The raw measurement text in the evidence retains the earlier 64 GB
wording; its measured values are unchanged and the target is now 96 GB.

Compatible public sources are [Comfy-Org/Wan-2.2](https://huggingface.co/Comfy-Org/Wan-2.2/tree/main/split_files/diffusion_models)
(`wan2.2_t2v_low_noise_14B_fp16.safetensors` and `wan2.2_t2v_high_noise_14B_fp16.safetensors`),
[Wan-AI/Wan2.1-T2V-14B](https://huggingface.co/Wan-AI/Wan2.1-T2V-14B/blob/main/models_t5_umt5-xxl-enc-bf16.pth)
(`models_t5_umt5-xxl-enc-bf16.pth`) and
[Comfy-Org/Wan-2.1](https://huggingface.co/Comfy-Org/Wan-2.1/blob/main/split_files/vae/wan_2.1_vae.safetensors)
(`wan_2.1_vae.safetensors`). Together these require approximately 68.9 GB before caches,
temporary transfers and checkpoints. Failed/resumed transfers can temporarily duplicate weights.

Low-noise training covers timesteps 0–875 with preserved distribution shape. High-noise alone
covers 875–1000 and teaches a different expert; its separately tested CLI recipe
uses `--lora_weight_high_noise` during generation. Combined expert training increases RAM
pressure. The pinned upstream warns that inactive-DiT offload on Windows can need about 96 GB
host RAM and cannot combine with block swapping. The initial recipe consequently trains one
expert and samples with both using lazy loading, applying the learned LoRA to low-noise only.
High-noise-only CLI training has now passed the native-weight scenario below.
Combined training has been evaluated from source, **not GPU-proven**.

## Production-size trial, 29 October 2026

[Issue #26](https://github.com/barnabyg/musubiGui/issues/26) ran the GUI recipe at
production size on the same RTX 3090 / 96 GB host with native FP16 experts. The
dataset was 28 owner-provided video clips at 16 fps. Every run used 832×480,
33 frames, head extraction, rank/alpha 16, learning rate 1e-4, batch one, gradient
checkpointing and runtime FP8 scaling. The
[evidence file](evidence/production-trial.json) records every attempt, its frozen
configuration and arguments, sampled resources and GPU throttle reasons.

| Attempt                        | Result                                              | Step time    |
| ------------------------------ | --------------------------------------------------- | ------------ |
| 36 swapped blocks, 280 W       | Trains; 21.0 GiB peak device VRAM                   | 93.8–94.5 s  |
| 39 swapped blocks              | Fails at step 0 in upstream FP8 linear patch (#31)  | —            |
| 36 blocks, 350 W, steeper fans | Trains                                              | 83.6 s       |
| 36 blocks, 280 W, steeper fans | 200 steps, checkpoints and state at 100/200, sample | 79.1 s, flat |

Job review accepts 0–38 swapped blocks; see the [qualification](#block-swap-qualification-30-october-2026)
of 37 and 38 below.

The 200-step run took 4 h 22 min. Peak sampled values were 21.1 GiB device VRAM,
18.2 GiB process-tree RSS and 34.1 GiB host RAM; RSS stayed at 16–18 GiB with no
growth. Step time is bounded by GPU thermal slowdown, active in 68–77% of loaded
samples at up to 68 °C core, not by VRAM or the power cap. Each rank-16 LoRA is
306,848,376 bytes and each optimizer-state folder about 879 MiB. The final save
duplicated the last interval save: identical tensors and state files, with only the
date, epoch and finish time differing in the checkpoint header. The GUI now removes
such proven copies after training (#34). The 20-step sample at 832×480 × 33 frames
took about 9 minutes and decoded cleanly.

This is the Measured Wan preset. 81 frames, 720×1280 and rank 32 were not tried.
Useful learning was not assessed: 200 steps is below the several hundred steps a
LoRA needs to change samples visibly, and the GUI cannot produce a no-LoRA baseline.

## Block-swap qualification, 30 October 2026

[Issue #42](https://github.com/barnabyg/musubiGui/issues/42) ran the same recipe and
dataset through the GUI's API for 10 steps each, at 280 W, with the cached latents and
text reused and sampling off. These test mechanics only, not learning
([evidence](evidence/block-swap-qualification.json)).

| Swapped blocks | Run        | Result                                       | Step time | Peak device VRAM |
| -------------- | ---------- | -------------------------------------------- | --------- | ---------------- |
| 37             | `c62b706e` | Trains; 10 steps and a 306,848,376-byte LoRA | 77.2 s    | 20.6 GiB         |
| 38             | `9ee3cb5b` | Trains; 10 steps and a 306,848,376-byte LoRA | 82.2 s    | 20.3 GiB         |

39 failed at step 0 in the upstream FP8 linear patch in #26 (run `dd98e24d`,
[evidence](evidence/production-trial.json)) and was not rerun, so 38 is the limit. The Measured Wan preset and the default stay at 36: these short runs show
that 37 and 38 train, not that they hold up over a long run or reduce step time.

## Network access

The pinned T5 code loads its tokenizer by repository name, even when `wan_t5` is a
local file. It fetches `Wan-AI/Wan2.1-T2V-14B` → `google/umt5-xxl` (about 21 MB) from
the Hugging Face Hub, without authentication. Latent caching passed offline on an empty
cache. Training takes no T5 input and passed offline with a seeded cache; it was not run
offline on an empty one. Text caching and generation need the tokenizer, and on an empty
cache with `HF_HUB_OFFLINE=1` both exit 1
with `OSError: We couldn't connect to 'https://huggingface.co' to load the files, and
couldn't find them in the cached files.` This was reproduced with the Hub switched off
through `HF_HUB_OFFLINE`, not by disabling the network
([evidence](evidence/wan-offline-tokenizer.json)).

The GUI therefore needs the Hub once. After a Wan stage succeeds, the Run's download is
checked against pinned SHA-256 hashes and kept in
`<cache root>/shared/wan-umt5-tokenizer`. Every later Wan stage verifies that copy, lays
it out in its own Run's Hugging Face cache and runs with `HF_HUB_OFFLINE=1`, so it cannot
contact the Hub. The stage log says so. If the copy is missing or damaged, or a download
doesn't match the hashes, the stage falls back to downloading as before.

A plain copy of the download is not enough. The repository has no `config.json` in that
folder. Online, the 404 gives an empty config, but offline transformers 5.17 treats the
same missing file as a connection error. Seeding adds an empty `config.json`, which gives
the same empty config. With it, all four stages passed offline, and the text cache was
byte-identical to one made online. A transformers upgrade could change this behaviour; a
stage that then fails shows the error above in its log. To recover, create an empty file
named `disabled` in the shared folder: stages then download with Hub access on, as they
did before seeding. Deleting the folder doesn't help, because the next successful
download publishes it again.

## Measured disk use

Job review's [disk estimate](../jobs.md) uses these sizes from the target PC.

| Item                                   | Bytes       |
| -------------------------------------- | ----------- |
| Rank-8 LoRA                            | 153,492,416 |
| Rank-16 LoRA                           | 306,848,376 |
| Rank-16 state folder                   | 920,958,520 |
| Latent cache, 832×480, 33 frames, each | 1,797,336   |
| Text cache, mean per item (28 clips)   | 397,532     |
| Hugging Face tokenizer files per Run   | 21,508,498  |
| Sample, 832×480, 33 frames             | 949,582     |
| Sample, 256×256, 17 frames             | 258,973     |

## Native-weight qualification

On 27 October 2026, the user-supplied native FP16 low/high-noise experts and native
`models_t5_umt5-xxl-enc-bf16.pth` passed fresh acceptance on the same RTX 3090 /
96 GB Windows machine and pinned training environment. The supplied DiTs each
contain 1,095 FP16 tensors with complete payload layouts. The existing Wan 2.1 VAE
was retained. No offline conversion was used; runtime FP8 DiT/T5 loading remains
part of the memory-saving recipe. See [native evidence](evidence/wan-native.json).

Through the GUI, real latent/text caching, two low-noise training steps, checkpoint
and optimizer-state saving, sample generation and Edge playback passed using
application revision `ae0b598` and its final atomic Windows launcher (protocol 3).
Restoring optimizer state as a new segment advanced the optimizer from step 2 to
3 and produced another playable sample. The original inputs and states remain
preserved. Upstream still resets its displayed counter and data position.

| Native scenario                | Latent cache (s) | Text cache (s) | Training (s) | Generation (s) |
| ------------------------------ | ---------------: | -------------: | -----------: | -------------: |
| Low-noise GUI                  |           27.041 |         26.319 |       34.496 |         95.667 |
| Restored low-noise GUI segment |           reused |         reused |       32.042 |         72.120 |
| High-noise CLI                 |            9.796 |         16.907 |       31.562 |         66.219 |

The separate high-noise CLI experiment used the same two-step, rank/alpha-eight,
256×256, 17-frame recipe. Starting from the generated `wan_stages` plan, only the
training `--dit` changed to the high-noise file, timestep bounds changed to
`--min_timestep 875 --max_timestep 1000`, and generation changed `--lora_weight`
to `--lora_weight_high_noise`. `--preserve_distribution_shape`, block swapping,
runtime FP8 and lazy generation loading were retained. Logs confirm the LoRA was
merged into the high-noise expert, then generation switched to the unmodified
low-noise expert. Its final checkpoint and all 17 decoded video frames passed;
Edge playback also passed. This CLI experiment does not add a high-noise GUI
recipe, qualify high-noise optimizer restoration or establish dual-expert training.

Both GUI samples and the CLI sample were 256×256, 1.0625-second videos. The
original GUI run peaked at sampled 19.07 GiB process-tree RSS / 8.37 GiB whole-device
VRAM; restoration peaked at 18.09 / 8.37 GiB. The CLI generation peak was 18.12 /
8.32 GiB. These are short sampled observations, not capacity limits or a controlled
performance comparison with the earlier converted-weight runs. The full commands,
logs, frozen inputs, state probes, telemetry and screenshots were local working
files and were not kept, so the paths in `evidence/wan-native.json` no longer resolve.
The earlier converted weights/evidence are preserved; main local Settings now
select the supplied native components, with the previous settings backed up.

Manual acceptance: run the command in the reference README; check every stage exits zero,
confirm the final checkpoint is nonempty, open the MP4 in Edge and check motion/playback.
Review the evidence's RAM/VRAM peaks and logs before increasing frames, geometry or batch size.
The fixture uses spatial multiples of 16 and `4*n+1` frames. This tiny smoke scenario does not
establish quality, full-length capacity or dual-expert training. Playback was automated
and muted; visual quality and useful learning have not been human-reviewed.

Pinned sources: [Wan guide](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/docs/wan.md),
[dataset contract](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/docs/dataset_config.md),
and [A14B configuration](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/wan/configs/wan_t2v_A14B.py).

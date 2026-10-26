# Wan 2.2 A14B T2V LoRA

The initial **candidate** trains the low-noise expert alone. It uses rank/alpha 8, batch one,
256×256, 17 frames at 16 fps, two optimizer steps, FP16 training, FP8 conversion from native
FP16 source, 36 swapped blocks, gradient checkpointing, AdamW and SDPA.
`reference.py` is the reusable command/config generator; each run saves resolved arguments
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
The existing local Wan experts use scaled FP8 and cannot be passed to this recipe. Loader
`--fp8_scaled` converts a supported source at runtime; it does not make saved scaled FP8 valid.
No conversion or overwrite of the user's existing files is performed. The fixed FP16 recipe
rejects BF16 DiTs before training; those require matching mixed precision and separate validation.

Actual preflight on 26 October 2026 recorded the existing revision/environment in
[wan-preflight.json](evidence/wan-preflight.json). The existing scaled FP8 experts and
Transformers-key T5 were rejected. No Wan cache, training, checkpoint or sample stage ran;
support remains **unresolved**, with no conclusion about hardware fit. Attempts to retrieve
native replacements produced only partial transfers, except for the small native Wan 2.1 VAE.
Downloads were stopped when the user elected to supply replacement files and continue development.

Compatible public sources are [Comfy-Org/Wan-2.2](https://huggingface.co/Comfy-Org/Wan-2.2/tree/main/split_files/diffusion_models)
(`wan2.2_t2v_low_noise_14B_fp16.safetensors` and `wan2.2_t2v_high_noise_14B_fp16.safetensors`),
[Wan-AI/Wan2.1-T2V-14B](https://huggingface.co/Wan-AI/Wan2.1-T2V-14B/blob/main/models_t5_umt5-xxl-enc-bf16.pth)
(`models_t5_umt5-xxl-enc-bf16.pth`) and
[Comfy-Org/Wan-2.1](https://huggingface.co/Comfy-Org/Wan-2.1/blob/main/split_files/vae/wan_2.1_vae.safetensors)
(`wan_2.1_vae.safetensors`). Together these require approximately 68.9 GB before caches,
temporary transfers and checkpoints. Failed/resumed transfers can temporarily duplicate weights.

Low-noise training covers timesteps 0–875 with preserved distribution shape. High-noise alone
covers 875–1000 and would teach a different expert; it needs a separately proven recipe and
uses `--lora_weight_high_noise` during generation. Combined expert training increases RAM
pressure. The pinned upstream warns that inactive-DiT offload on Windows can need about 96 GB
host RAM and cannot combine with block swapping. The initial recipe consequently trains one
expert and samples with both using lazy loading, applying the learned LoRA to low-noise only.
High-noise-only and combined training have been evaluated from source, **not GPU-proven**.

Manual acceptance: run the command in the reference README; check every stage exits zero,
confirm the final checkpoint is nonempty, open the MP4 in Edge and check motion/playback.
Review the evidence's RAM/VRAM peaks and logs before increasing frames, geometry or batch size.
The fixture uses spatial multiples of 16 and `4*n+1` frames. This tiny smoke scenario does not
establish quality, full-length capacity, high-noise training, dual-expert training or 64 GB fit.

Pinned sources: [Wan guide](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/docs/wan.md),
[dataset contract](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/docs/dataset_config.md),
and [A14B configuration](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/wan/configs/wan_t2v_A14B.py).

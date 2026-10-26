# Offline Wan weight conversion

Use this when the existing installation has the legacy Comfy scaled-FP8 Wan
experts and Transformers-key scaled-FP8 UMT5, while the pinned musubi-tuner recipe
requires native FP16 inputs. It is an alternative to downloading full-precision
replacements. **Dequantizing FP8 does not restore original full precision.**

The converter writes new files and refuses existing destinations, unknown T5
keys, missing/non-scalar scales, unsupported tensor dtypes, invalid bounds,
nonfinite FP8 values and out-of-range weight scales. Temporary files are removed
on failure; original inputs remain unchanged. Allow approximately 68.52 GB for
the two derived DiTs and text encoder, plus a 1 GiB write safety margin and space
for caches/checkpoints. Keep the original files until the converted paths have
been validated for your workflow.

Run from the application checkout after `launch.cmd` has prepared its Python:

```powershell
.venv\Scripts\python.exe -m musubi_gui.convert_wan dit --source C:\existing\wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors --destination C:\derived\wan-low-dequantized-fp16.safetensors
.venv\Scripts\python.exe -m musubi_gui.convert_wan dit --source C:\existing\wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors --destination C:\derived\wan-high-dequantized-fp16.safetensors
.venv\Scripts\python.exe -m musubi_gui.convert_wan t5 --source C:\existing\umt5_xxl_fp8_e4m3fn_scaled.safetensors --destination C:\derived\wan-t5-dequantized-fp16.safetensors
```

Add `C:\derived` to Settings → Model roots. Set `wan_low`, `wan_high` and `wan_t5`
to the corresponding new files, retaining a compatible native Wan 2.1 VAE.
Run diagnostics, review the job and execute the short reference before increasing
geometry or training length.

Each output embeds a lossy-source notice and has a `.provenance.json` sidecar with
source/destination locations, sizes, source timestamp, header identities and the
conversion method. Header identities are not whole-file content hashes. Source
size/timestamp changes detected during conversion prevent publication. Files
remain local and ignored by Git; retain their original source/model licences.

The numerical rule is `FP16(float(FP8_weight) * scalar_scale_weight)`.
`scale_input` controls activation quantization and is not a weight multiplier;
it is omitted when producing normal floating-point weights. The pinned upstream
uses the same multiplication in `ScaledFP8Linear.forward` in
`src/musubi_tuner/modules/fp8_optimization_utils.py`. T5 mapping follows the
attention, gated FFN, per-block relative-position and normalization fields in
`src/musubi_tuner/wan/modules/t5.py`; embedded tokenizer bytes are not model
parameters. The selected upstream continues to provide its own tokenizer.

This conversion was followed by real latent/text caching, two training steps,
checkpoint saving, generation and Edge sample playback on the target machine.
See [Wan results](wan.md). The output is lossy-derived model data, not a new
full-precision release; quality and other architectures/formats are unqualified.

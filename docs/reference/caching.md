# Musubi Tuner caching

Investigation for #136, against the installed, unmodified tracked checkout at
`f8a1b03794a49239a3539015075f5123d6c07d66`. Commands used were
`git -C C:/ai/musubi-tuner rev-parse HEAD`, `git status --porcelain`, and source
inspection with `rg` and `Get-Content`. Untracked local command/configuration files
do not change the pinned implementation.

## Required preparation

Both recipes train from cached latents and text embeddings. The dataset loader
discovers latent files and skips entries without matching text cache files; these
recipes have no supported on-the-fly alternative. See the upstream
[image loader](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/dataset/image_video_dataset.py#L603)
and [video loader](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/dataset/image_video_dataset.py#L1032).
H3 guidance loss also requires the unconditional embedding produced by text caching:
[trainer requirement](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/minimax_h3_train_network.py#L827).

## Storage, reuse and invalidation

Cache files live in each dataset section's configured cache directory. Latent names
include the source basename, source dimensions and architecture. Text names end in
`_<architecture>_te.safetensors`. Video latent keys include crop position and frame
count; H3 text is per crop, whereas Wan text is per original clip. These names alone
do not establish content validity. See
[paths](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/dataset/image_video_dataset.py#L175)
and [video text lookup](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/dataset/image_video_dataset.py#L1051).

Upstream `--skip_existing` uses file existence by default, including Wan. It does
not prove that media, captions or encoding settings are unchanged. H3 adds metadata
comparisons for source/model fingerprints, task, crop, cache format and encoding
settings. See
[latent processing](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/cache_latents.py#L290),
[text processing](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/cache_text_encoder_outputs.py#L75),
[H3 latent validation](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/minimax_h3_cache_latents.py#L656),
and [H3 text validation](https://github.com/kohya-ss/musubi-tuner/blob/f8a1b03794a49239a3539015075f5123d6c07d66/src/musubi_tuner/minimax_h3_cache_text_encoder_outputs.py#L483).

The cachers normally delete files that do not belong to the current section.
`--keep_cache` prevents that deletion; it does not validate stale files. Therefore
sections and Runs must not share a mutable cache directory. The GUI gives each
Run private cache storage and each section a separate directory. Reuse copies
hash-verified complete artifacts from a compatible Run, including H3's unconditional
embedding. Rebuild bypasses this reuse and runs the encoder in the new private
directory. Neither choice deletes or modifies another Run's caches.

The GUI's separate stage identities include frozen inputs, relevant model identities,
geometry/preparation settings, source checkout and runtime versions. Captions
invalidate text caching; changed media invalidates latents; resolution and layout
changes invalidate affected stage identities conservatively. Changing reuse policy
does not change encoded content, so policy is excluded from cache identity. Retry
retains the frozen policy with a new private cache directory. Restore continues to
use frozen inputs; changing a Job does not rewrite a prepared Run.

## Controls and manual verification

Each stage offers **Reuse compatible cache** (the default) or **Rebuild cache**.
Configuration review shows the policy; actual reuse is decided during preparation
after verifying candidate artifacts. Run stages report whether they were reused.
No cache-disable option is offered because both stages are required by these recipes.

From the repository root in PowerShell 7, prepare and launch an isolated CPU fixture:

```powershell
.\acceptance.cmd --workspace ".cache/cache-check-$([guid]::NewGuid().ToString('N'))"
```

Open WAN acceptance 1, expand Advanced controls and find Caching. Prepare the job
with the default policies. Change training steps to create another revision and
prepare again: both cache stages should be reused. Set only text caching to Rebuild
cache, save and prepare: latent caching should be reused and text caching should
run again. Earlier Runs remain startable. Reload the Job and confirm the policy
persists. Repeat for H3; its text stage includes the unconditional embedding.
The fixture verifies orchestration and persistence, not GPU execution or quality.

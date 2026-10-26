# GPU reference workflows

These are CLI acceptance scenarios for issues #1 and #2, independent of future GUI
dataset/job execution. They call the **existing** musubi-tuner installation and do not
install, upgrade, patch, or copy its training code. Ordinary verification does not run a GPU.

1. Run `launch.cmd`, save installation/model/output/cache paths in Settings, and run diagnostics.
2. Stop the GUI if desired. Its backend is not required by these commands.
3. Run one scenario at a time from the repository root:

```powershell
.venv\Scripts\python.exe -m musubi_gui.reference wan
.venv\Scripts\python.exe -m musubi_gui.reference h3
```

Each command prepares an original synthetic moving-square fixture, writes dataset TOML,
executes latent caching → text caching → two LoRA training steps → sample generation,
then decodes the sample. H3 adds a same-stem PCM sine-tone sidecar and requires 100%
real-audio supervision in the latent-cache summary and decodable generated audio.
The synthetic fixture media/captions are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
They contain no downloaded/private media and measure execution rather than model quality.

Prerequisites: native Windows 11, RTX 3090, Git, the selected existing Python with the
pinned upstream's dependencies, and the components listed in the family guides.
Recipes pin upstream revision `f8a1b03794a49239a3539015075f5123d6c07d66`.
The runner rejects other revisions; it never checks out or upgrades the selected install.
This pin is a tested integration candidate, not a promise of upstream release stability.

Use `--settings C:\absolute\settings.json` for a separate saved setup, `--plan-only` to
validate and save commands without training, and `--stage-timeout 1800` to bound each stage.
Plan-only output always leaves support unresolved. Missing prerequisites exit 2 and retain
evidence where valid output/cache roots are configured; invalid setup paths fail before writes.
Ctrl+C stops the stage process tree and records an interrupted run.

Every attempt has unique output/cache directories. No stale checkpoint/sample can satisfy
a later attempt. `evidence.json` records exact commands, environment, revision, component
sizes/header identities, stage exits, timings, sampled process-tree RSS, host RAM use,
whole-device VRAM and free-disk deltas. Logs redact known environment secret values,
HF tokens, bearer tokens, URL credentials and URL queries. Models/caches/checkpoints/logs
and local settings remain ignored. No credentials should be entered into component paths.

Measurement limits: sampling can miss brief peaks and process-tree RSS can double-count
shared pages. Whole-device VRAM includes unrelated GPU applications. Disk deltas include
unrelated filesystem activity and are **not** a complete capacity estimate. The runner stops
a stage if observed process-tree RSS exceeds 60 GiB, but this polling guard is not a hard
memory cap. The measured host has 102,665,969,664 bytes (~95.6 GiB) usable RAM, matching
the user's corrected nominal **96 GB** target. Historical evidence mentioning a 64 GB
target is retained as recorded; that target was superseded. Current successful scenarios
pass the execution support gate on this machine. Human playback/quality review remains
recommended. Successful smoke tests do not demonstrate useful learning or
production-resolution performance.

Follow [Wan](wan.md) and [H3](h3.md) for recipes, actual evidence, limitations and manual checks.
The runner is intentionally limited to these recipes: no I2V/Ref2VA, full fine-tuning,
caption automation, remote hosting, job queue or resume support is introduced.

Upstream code and weights retain their respective licences. In particular, consult the
[MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
and the model cards before using or redistributing weights. We distribute neither weights
nor upstream code. AI Toolkit is only an interface reference for the setup application.

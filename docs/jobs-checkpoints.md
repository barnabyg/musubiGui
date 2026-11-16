# Checkpoints and disk estimates

Checkpoint retention and the disk estimate in review. [Jobs](jobs.md) lists the other job
topics.

## Checkpoint retention

In the Checkpoints tab, set **Maximum checkpoint saves to keep**.
Zero (the default, including older jobs) keeps unlimited history. A positive value keeps
that many historical saves per expert plus the newest/final save. A save includes its
LoRA and optimizer/training state when enabled. Wan protects the latest restorable pair,
pending phase/sampling inputs, and the counterparts needed to restore retained pairs.
Other Runs' frozen inputs and restoration sources are also protected, so these exceptions
can exceed the limit. Sampling results are never pruned.

During training a separate worker checks about every five seconds and at phase completion. It
only prunes older validated saves once a newer complete save exists. Incomplete saves and
unknown files are kept. Files are first quarantined under the Run's checkpoints folder;
a durable journal allows interrupted removals to resume at the next check or saved-state
inspection. Run details show removal counts, protected exceptions and errors. A removal
failure does not fail training; inspect its reported error before removing a quarantine
remainder manually. Quarantined and intentionally pruned artifacts are not offered as outputs
or restoration choices. Restored segments start their own retention journal.
Inspection runs independently of training process supervision so Stop remains responsive.
Closing the service cancels chunked hashing, terminates/reaps its state probe (including
the Windows interpreter-launcher child), and joins the inspection worker. This does not
terminate training processes, which retain the existing interrupted-run recovery behaviour.

Review reports expected retained saves before protected exceptions. The disk budget remains
a conservative peak upper bound: short step intervals can accumulate saves between checks,
and protected restoration sources cannot be counted as reclaimable storage.

To verify manually, run from the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/retention-$([guid]::NewGuid().ToString('N'))" --wan-paired
```

Open WAN acceptance 1, set Training steps to 12 in Training, then steps per expert
to 2 in Job & dataset. In Checkpoints, set Save every N steps to 1 and Maximum checkpoint
saves to keep to 1. Review, then Prepare
and start. Expect older saves to be pruned while both final expert LoRAs and their latest
restorable pair remain available. Inspect Run details for retention status, restore the latest
pair with **Train until step** four steps after its saved step, and confirm the new segment
completes. Zero should retain
history as before. This command uses synthetic CPU workers and does not qualify GPU training.

## Disk estimates

Review also estimates the disk a Run will use: the media snapshot, caches, LoRA
checkpoints, optimizer states and samples, plus the free space on each target volume.
When the output and cache roots share a volume, their needs are added together. A Run
needs the estimate plus a margin of 10% or 1 GB, whichever is larger.

If a volume is short, review shows a warning naming the volume and the shortfall. It is
only a warning: Prepare still goes ahead, because the estimate can be wrong in either
direction and large runs are normal. A Run that needs tens of GB does not warn when the
space is free.

The estimate uses sizes measured on the target PC
([Wan](reference/wan.md#measured-disk-use), [H3](reference/h3.md#measured-disk-use)):

- Checkpoints are linear in rank for each model. Each saved state is 3.0014 times its
  checkpoint.
- The save count is `floor(steps / save_every) + 1`. This is the peak: when the last step
  falls on the interval, the duplicate exists until training succeeds and it is removed,
  so a successful Run keeps one set fewer. New alternating Wan Runs write that final
  step once, so this remains a conservative estimate for their checkpoint/state storage.
- Wan latents are computed from size and frames, and H3 latents are fitted to two measured
  sizes at 124 frames. H3 image latents use the same rate, and were measured at one size. Each
  cached video window or image adds one latent file, and repeats add none.

Its limits:

- It covers one Run and does not count other queued Runs. Only review shows it, so Retry
  and Restore as new segment don't warn on low disk.
- H3 frame counts above 124, H3 ranks other than 4 and 8, and Wan ranks other than 8 and
  16 are extrapolated.
- Text caches use the mean per-item size measured in the #26 trial, so long captions use
  more. Samples use the largest measured bytes per pixel-frame, and video content changes
  the real size. One-frame native Wan PNG previews instead reserve four bytes per pixel
  plus 4 kB per image, so lossless images do not inherit the compressed-video estimate.
- Bucketing can change latent sizes.
- Compiler caches are left to the margin.
- Free space is read at review. It is not checked again at Prepare or Start, so a Run can
  still run out if the space is used in the meantime.

Compared with run `fb66e3d3` in #26 (Wan, 832×480, 33 frames, rank 16, 200 steps, saving
every 100 with state), the estimate gives 83 MB of caches against 83 MB measured, 921 MB
of checkpoints against 921 MB, and 2.76 GB of states against 2.76 GB. The sample is
estimated at 3.3 MB against 0.9 MB measured.

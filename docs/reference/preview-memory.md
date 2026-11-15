# Preview GPU memory selection

Newly prepared Runs choose standalone preview/final-generation residency separately
from training. **Samples → Generation memory policy** offers automatic selection
with reserved headroom, or the conservative family preset. Configuration review,
the frozen stage record and generation diagnostics explain the resolved choice.
Changing a Job never rewrites an existing Run's command. Old Jobs and exports
remain readable; an absent policy means automatic selection for future Runs.
Retries and restored segments retain the source Run's frozen generation settings.

## Conservative selection

Automatic reduction requires one RTX 3090 reporting exactly 24 GiB, consistent
free/total memory, the pinned recipe, supported model representations and an exact
qualified output geometry. Qualification covers at most four inference steps,
256 prompt characters, Wan rank at most 8, or H3 rank at most 4. Other settings,
capacities/devices, missing GPU data and unmeasured geometries use the conservative
preset: Wan 38 or H3 48 swapped blocks. Fallback is not a promise that arbitrary
output sizes fit. Wan nominally permits 39 upstream, but its pinned FP8 training
patch failed at 39; this application keeps the established supported maximum 38.

The budget starts from a rounded-up measured baseline envelope, adds the cost of
extra resident transformer blocks and current GPU use above the reference desktop
allowance of 1 GiB, and still reserves **5 GiB**. The reserve covers unsampled
transients, text conditioning, VAE/audio decoding, allocator overhead and adapter
allocations. Current external GPU allocations are charged in addition to it.
Sampled peaks are never treated as the true maximum.

| Path                                                                                           | Exact width × height × frames | Baseline envelope  | Extra resident block | Lowest automatic swap count |
| ---------------------------------------------------------------------------------------------- | ----------------------------- | ------------------ | -------------------- | --------------------------- |
| Paired Wan, native FP16 sources converted to scaled FP8 at load, lazy experts, Torch attention | 256 × 256 × 17                | 9 GiB at 36 swaps  | 384 MiB              | 16                          |
| Same paired Wan path                                                                           | 832 × 480 × 33                | 12 GiB at 36 swaps | 384 MiB              | 18                          |
| H3, pruned ConvRot INT8, SDPA, joint video/audio                                               | 256 × 256 × 124               | 9 GiB at 48 swaps  | 512 MiB              | 32                          |

The selector keeps more blocks off GPU when other GPU use consumes the budget. It
never goes below the qualified minimum. Wan's two experts are loaded lazily; both
represented sources are checked. H3 bounds the INT8 transformer, quantized 32B text
encoder and separate video/audio VAEs. It keeps 50 text-encoder blocks swapped.
Attention mode, quantization, learned adapter routing and output geometry remain
unchanged.

Native Wan/H3 previews retain training residency and optimizer tensors. The pinned
trainer exposes inference/training mode switches, but no safe public interface
for disposing/rebuilding its offloader. H3 H2D streaming also retains CPU masters
and GPU ring bindings. Rebuilding these without validated ownership/restoration
risks training correctness, so Runs explain this fallback. Real two-step Wan/H3
probes verified two previews, another optimizer update and changed checkpoint
tensor payloads after the first preview. H3 video and audio decode successfully.

A newly prepared automatic standalone command can retry an explicit CUDA capacity
failure **once** at the conservative count. The failed child process exits first,
releasing CUDA allocations. Stop disables recovery, other errors are not retried,
and an already written output is preserved rather than overwritten. Failure of
the fallback is terminal. Native training is not restarted after an OOM.

## Measurements and limits

The [GPU evidence](evidence/preview-memory.json) records settings, source identities,
wall times, phase attribution, VRAM/RAM peaks, decoded media and continuation. The
RTX 3090/96 GB Windows host ran one GPU operation at a time. Existing models,
datasets, Runs and installed dependencies were read-only. Caches and outputs were
isolated in a task worktree.

Cold file/kernel caches and model loading materially affect these short runs.
Comparative wall times are observations, not a general speedup guarantee. H3 at
640 × 352 × 124 took 80.83 s with 48 swaps and 83.42 s with 32 swaps, so that
geometry retains 48. Larger ranks, longer sampling, other frame grids, portrait
transposes, GPU consumers appearing after preparation and native residency
reductions remain unqualified. CPU fixtures establish orchestration,
configuration and persistence, not GPU memory safety or performance.

Qualification-only warning exception: installed `einops/einops.py:847` emits
`SyntaxWarning: invalid escape sequence '\s'` under Python 3.12 when its
mathematical docstring is compiled without bytecode writes. The first raw report
is retained in local GPU logs. It does not affect the executed expression;
installed dependencies were deliberately preserved. Review this exact exception
when updating einops, Python or the pinned recipe. Canonical application
verification has no added warning suppression.

## Manual comparison

From `C:/docs/git/musubiGui` in PowerShell 7, this one command prepares and launches
an isolated GPU application:

```powershell
.\acceptance.cmd --mode gpu --settings .state/settings.json --preview-images --wan-paired --workspace ".cache/preview-memory-$([guid]::NewGuid().ToString('N'))"
```

Prerequisites: the supported managed installation, configured local Wan/H3 models,
Windows/Edge, uv, npm and internet access for small processor/tokenizer files if
uncached. Captioned synthetic images are created; they test execution, not learning
quality. Existing application state is preserved.

In WAN acceptance 1 choose 2 steps, expert interval 1, previews every step, four
sample inference steps, rank 8 and 256 × 256 × 17 sample geometry. Select
**Conservative family preset**, review and run. Duplicate the Job, choose
**Automatic with reserved headroom**, review and run after the first ends. Compare
reviewed memory explanations, stage logs and elapsed generation time; watch GPU
use. Expect playable previews, final low/high adapters and training continuation.
Repeat with H3 acceptance 1, rank 4, 2 steps, previews every step and
256 × 256 × 124. Expect video and audio; native previews explain that training
memory settings are retained. Reload and reopen Runs to confirm their frozen
choice persists. To check the larger Wan profile, use 832 × 480 × 33 while retaining
rank 8 and four sampling steps.

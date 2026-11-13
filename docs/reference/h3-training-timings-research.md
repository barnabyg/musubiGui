# H3 LoRA training timing research

Researched 13 November 2026. Scope: compare an RTX 3090 run at 640×352 (or portrait transpose), 124 frames, rank 32, batch one, guidance loss, gradient checkpointing and 48 H2D-only swapped blocks. User reports roughly 23 hours for 1,000 steps, equivalent to 82.8 seconds per step if that estimate covers the whole run.

## Findings

There is insufficient matched public evidence to establish a typical speed for this exact recipe. Faster H3 training is reported, but image/video length, loss recipe, quantization and offloading differ or are omitted. Do not describe 83–90 seconds as a confirmed community norm, or attribute the difference to rank without a controlled measurement.

| Hardware and source                                                                                                                                                 | Reported training speed                          | Settings and comparison limits                                                                                                                                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RTX 3090, this repository's [production trial](h3.md#production-size-trial-29-october-2026)                                                                         | 70–72 s/step                                     | 640×352, 124 frames, rank 8, 48 H2D-only swaps, guidance loss, checkpointing, 245 W. Closest documented recipe; not an independent report.                                                                        |
| RTX 4090, [AI Toolkit user report](https://www.reddit.com/r/StableDiffusion/comments/1vxiw4d/minimaxh3_lora_training_on_aitoolkit_64gb_ram_rtx/)                    | About 6 s/step                                   | Windows 11, 64 GB RAM; resolution setting 448, example 512×384 video buckets; 35 of 50 blocks offloaded. Frame count, rank and loss recipe missing. Cannot establish equivalence to 124-frame training.           |
| RTX 4090, [Musubi fork author's image-only test](https://www.reddit.com/r/StableDiffusion/comments/1vfa048/experimental_minimax_h3_lora_training_on_24_gb/)         | About 2.5 s/step; about one hour for 1,500 steps | 1024×1024 stills, rank/alpha 16, batch one, INT8 base, 15 swapped blocks. Author explicitly confirms image-only training. Not a video comparator.                                                                 |
| RTX 5090, [SECourses trainer maintainer](https://github.com/FurkanGozukara/SECourses_Musubi_Trainer/blob/main/README.md)                                            | About 8.5 s/iteration                            | Lowest-VRAM video preset: 256px, 124 frames, heavy swapping. Different GPU and substantially smaller geometry. The same source confirms a 384px/124-frame RTX 3090 test at 22.5 GB peak, but gives no 3090 speed. |
| RTX PRO 4500 Blackwell 32 GB, [OmniChar author's video test](https://www.reddit.com/r/StableDiffusion/comments/1vhy5vl/minimax_h3_lora_training_with_short_videos/) | 3.01 s/step                                      | Author clarifies this is 512×512, one-second clips, 4-bit training, video path only with audio omitted. Different workload and hardware.                                                                          |

## Lower-confidence lead

The search index for a [Reddit captioning discussion](https://www.reddit.com/r/StableDiffusion/comments/1xowfxi/minimaxh3_captioning_dataset_for_lora_training/) exposes a table claiming RTX 3090 24 GB, INT8, batch one and 40 swapped blocks. Its 124-frame rows report 17.6 s/step at 384px (0.15 MP), 21.4 at 512px (0.26 MP), 27.7 at 640px (0.41 MP), and 28.7 at 768px (0.59 MP). The directly opened page did not expose that comment, so its author/context could not be checked. Rank, trained block range, guidance loss, checkpointing and audio policy are unresolved. Treat this as an unverified community claim to investigate, not a validated benchmark. It suggests possible room for improvement but establishes neither a target speed nor a specific cause.

## Recipe differences

[Upstream Musubi documentation](https://github.com/kohya-ss/musubi-tuner/blob/main/docs/minimax_h3.md) describes guidance loss as adding a no-gradient forward on about 85% of steps, with an approximate 1.5× step cost relative to the training-adapter route. This is a recipe-level estimate, not a prediction for this machine. It also states that merging a training adapter requires BF16 source weights; it cannot simply be merged into the already quantized INT8 base used here.

Rank 32 versus 8 increases LoRA parameter count roughly fourfold when target modules are unchanged. It does not multiply the entire transformer workload by four. No controlled rank comparison was performed in this research.

## Practical interpretation

The user's estimate is reasonably close to the existing local recipe measurement, but independent reports do not confirm it as typical. To determine whether the current run has avoidable overhead, inspect steady-state step times, actual emitted arguments, GPU utilization/power/temperature, RAM pressure and offload behavior. Change one setting at a time in any future comparison. No training settings or running processes were changed for this research.

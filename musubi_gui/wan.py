"""Wan 2.2 A14B recipe for the pinned musubi-tuner revision."""

from pathlib import Path

from musubi_gui.recipe import CAPTION, Stage, cli, python_args
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import Settings


def validate_wan(settings: Settings) -> list[str]:
    errors = []
    for name in ["wan_low", "wan_high", "wan_vae", "wan_t5"]:
        value = settings.models.get(name, "")
        if not value or not Path(value).is_file():
            errors.append(f"Missing {name}; select compatible model files in Settings.")
            continue
        file = Path(value)
        if name in {"wan_low", "wan_high"}:
            if file.suffix != ".safetensors":
                errors.append(f"{name}: this recipe requires native FP16 safetensors DiT weights.")
                continue
            header = safetensors_header(file)
            if any("scale_weight" in k or "scale_input" in k for k in header) or any(
                isinstance(v, dict) and str(v.get("dtype", "")).startswith("F8") for v in header.values()
            ):
                errors.append(
                    f"{name}: pre-quantized/scaled FP8 input is unsupported by this recipe. Use FP16 sources; --fp8_scaled converts at load time."
                )
            elif header.get("blocks.0.cross_attn.k.weight", {}).get("dtype") != "F16":
                errors.append(
                    f"{name}: this reference recipe requires FP16 DiT weights to match its FP16 mixed precision. BF16 needs a separately validated recipe."
                )
            if "patch_embedding.weight" not in header:
                errors.append(
                    f"{name}: expected native Wan patch_embedding weights; Diffusers shards cannot be passed directly."
                )
        if name == "wan_vae" and file.suffix == ".safetensors":
            header = safetensors_header(file)
            shape = header.get("conv2.weight", {}).get("shape", [])
            if not shape or shape[0] != 16:
                errors.append(
                    "wan_vae: A14B requires the native 16-channel Wan 2.1 VAE; Wan 2.2 5B and unconverted Diffusers VAEs are incompatible."
                )
        if name == "wan_t5" and file.suffix == ".safetensors":
            header = safetensors_header(file)
            if "token_embedding.weight" not in header:
                errors.append("wan_t5: use native Wan UMT5 weights, not Transformers shards or scaled FP8 weights.")
    return errors


def wan_stages(settings: Settings, run: Path, cache: Path) -> list[Stage]:
    model = settings.models
    dataset = str(run / "dataset.toml")
    output = str(run / "checkpoints")
    return [
        Stage(
            "fixture",
            [*python_args(settings), str(Path(__file__).with_name("fixture.py")), "wan", str(run / "fixture")],
        ),
        Stage(
            "latent_cache",
            cli(
                settings,
                "wan_cache_latents.py",
                "--dataset_config",
                dataset,
                "--vae",
                model["wan_vae"],
                "--batch_size",
                "1",
                "--num_workers",
                "1",
                "--vae_cache_cpu",
            ),
        ),
        Stage(
            "text_cache",
            cli(
                settings,
                "wan_cache_text_encoder_outputs.py",
                "--dataset_config",
                dataset,
                "--t5",
                model["wan_t5"],
                "--batch_size",
                "1",
                "--num_workers",
                "1",
                "--fp8_t5",
            ),
        ),
        Stage(
            "train",
            [
                *python_args(settings),
                "-m",
                "accelerate.commands.launch",
                "--num_processes",
                "1",
                "--num_machines",
                "1",
                "--dynamo_backend",
                "no",
                "--num_cpu_threads_per_process",
                "1",
                "--mixed_precision",
                "fp16",
                str(Path(settings.checkout) / "wan_train_network.py"),
                "--task",
                "t2v-A14B",
                "--dit",
                model["wan_low"],
                "--dataset_config",
                dataset,
                "--sdpa",
                "--mixed_precision",
                "fp16",
                "--fp8_base",
                "--fp8_scaled",
                "--blocks_to_swap",
                "36",
                "--gradient_checkpointing",
                "--optimizer_type",
                "AdamW",
                "--learning_rate",
                "1e-4",
                "--max_data_loader_n_workers",
                "0",
                "--network_module",
                "networks.lora_wan",
                "--network_dim",
                "8",
                "--network_alpha",
                "8",
                "--timestep_sampling",
                "shift",
                "--discrete_flow_shift",
                "3.0",
                "--min_timestep",
                "0",
                "--max_timestep",
                "875",
                "--preserve_distribution_shape",
                "--max_train_steps",
                "2",
                "--save_every_n_steps",
                "1",
                "--seed",
                "42",
                "--output_dir",
                output,
                "--output_name",
                "wan-reference",
            ],
        ),
        Stage(
            "generate",
            cli(
                settings,
                "wan_generate_video.py",
                "--task",
                "t2v-A14B",
                "--dit",
                model["wan_low"],
                "--dit_high_noise",
                model["wan_high"],
                "--vae",
                model["wan_vae"],
                "--t5",
                model["wan_t5"],
                "--fp8",
                "--fp8_scaled",
                "--fp8_t5",
                "--blocks_to_swap",
                "36",
                "--lazy_loading",
                "--attn_mode",
                "torch",
                "--video_size",
                "256",
                "256",
                "--video_length",
                "17",
                "--fps",
                "16",
                "--infer_steps",
                "4",
                "--seed",
                "42",
                "--prompt",
                CAPTION,
                "--lora_weight",
                str(run / "checkpoints/wan-reference.safetensors"),
                "--save_path",
                str(run / "samples"),
                "--output_type",
                "video",
            ),
        ),
    ]

"""MiniMax H3 guidance-loss recipe for the pinned musubi-tuner revision."""

from pathlib import Path

from musubi_gui.recipe import CAPTION, Stage, cli, python_args
from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.settings import Settings


def validate_h3(settings: Settings) -> list[str]:
    errors = []
    for name in ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]:
        value = settings.models.get(name, "")
        if not value or not Path(value).is_file():
            errors.append(f"Missing {name}; choose compatible model files in Settings.")
            continue
        file = Path(value)
        if file.suffix != ".safetensors":
            errors.append(f"{name}: the pinned H3 recipe requires released safetensors components.")
            continue
        header = safetensors_header(file)
        if any(
            isinstance(v, dict)
            and str(v.get("dtype", "")).startswith("F8")
            and not (name == "h3_text" and k.endswith(".weight_scale"))
            for k, v in header.items()
        ):
            errors.append(f"{name}: H3 does not support FP8 artifacts.")
        if name == "h3_dit" and any("qweight" in k or "qzeros" in k for k in header):
            errors.append(
                "h3_dit: NVFP4/AWQ transformer artifacts are unsupported. Use released FL2VA BF16 or ConvRot INT8."
            )
    return errors


def h3_stages(settings: Settings, run: Path, cache: Path) -> list[Stage]:
    model = settings.models
    dataset = str(run / "dataset.toml")
    uncond = str(cache / "uncond.safetensors")
    return [
        Stage(
            "fixture", [*python_args(settings), str(Path(__file__).with_name("fixture.py")), "h3", str(run / "fixture")]
        ),
        Stage(
            "latent_cache",
            cli(
                settings,
                "minimax_h3_cache_latents.py",
                "--dataset_config",
                dataset,
                "--task",
                "t2va",
                "--video_vae",
                model["h3_video_vae"],
                "--audio_vae",
                model["h3_audio_vae"],
                "--cache_seed",
                "42",
                "--num_workers",
                "1",
            ),
        ),
        Stage(
            "text_cache",
            cli(
                settings,
                "minimax_h3_cache_text_encoder_outputs.py",
                "--dataset_config",
                dataset,
                "--task",
                "t2va",
                "--text_encoder",
                model["h3_text"],
                "--text_encoder_blocks_to_swap",
                "50",
                "--text_cache_dtype",
                "bf16",
                "--uncond_output",
                uncond,
                "--num_workers",
                "1",
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
                "--mixed_precision",
                "bf16",
                "--dynamo_backend",
                "no",
                "--num_cpu_threads_per_process",
                "1",
                str(Path(settings.checkout) / "minimax_h3_train_network.py"),
                "--dataset_config",
                dataset,
                "--task",
                "t2va",
                "--dit",
                model["h3_dit"],
                "--network_module",
                "networks.lora_minimax_h3",
                "--network_dim",
                "4",
                "--network_alpha",
                "4",
                "--sdpa",
                "--mixed_precision",
                "bf16",
                "--gradient_checkpointing",
                "--block_swap_h2d_only",
                "--blocks_to_swap",
                "48",
                "--optimizer_type",
                "AdamW",
                "--learning_rate",
                "1e-4",
                "--max_train_steps",
                "2",
                "--save_every_n_steps",
                "1",
                "--max_data_loader_n_workers",
                "0",
                "--seed",
                "42",
                "--output_dir",
                str(run / "checkpoints"),
                "--output_name",
                "h3-reference",
                "--h3_guidance_loss_scale",
                "4",
                "--h3_guidance_loss_sigma_min",
                "0.15",
                "--h3_guidance_loss_uncond_cache",
                uncond,
            ],
        ),
        Stage(
            "generate",
            cli(
                settings,
                "minimax_h3_generate_video.py",
                "--task",
                "t2va",
                "--dit",
                model["h3_dit"],
                "--video_vae",
                model["h3_video_vae"],
                "--audio_vae",
                model["h3_audio_vae"],
                "--text_encoder",
                model["h3_text"],
                "--text_encoder_blocks_to_swap",
                "50",
                "--attn_mode",
                "sdpa",
                "--prompt",
                CAPTION + " A steady sine tone plays.",
                "--video_size",
                "256",
                "256",
                "--video_length",
                "124",
                "--infer_steps",
                "4",
                "--seed",
                "42",
                "--blocks_to_swap",
                "48",
                "--lora_weight",
                str(run / "checkpoints/h3-reference.safetensors"),
                "--save_path",
                str(run / "samples/sample.mp4"),
            ),
        ),
    ]

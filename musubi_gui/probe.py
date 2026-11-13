"""Read-only probe, executed by the explicitly selected training Python."""

import argparse
import importlib.metadata
import json
import os
import platform


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--versions-only", action="store_true", help="Read package metadata without initializing Torch/CUDA."
    )
    args = parser.parse_args()
    packages: dict[str, str | None] = {}
    for name in ["torch", "accelerate", "safetensors", "transformers", "av", "musubi-tuner", "triton-windows"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result: dict[str, object] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
    }
    preprocessors: dict[str, str | None] = {}
    for name in ["pillow", "numpy", "tokenizers", "sentencepiece", "torchvision", "torchaudio", "scipy", "einops"]:
        try:
            preprocessors[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            preprocessors[name] = None
    result["preprocessing_packages"] = preprocessors
    if args.versions_only:
        print("MUSUBI_PROBE=" + json.dumps(result))
        return
    if os.environ.get("MUSUBI_REFERENCE_PROBE") == "1":
        result["reference_cache_environment"] = {
            name: os.environ.get(name) for name in ["HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_XET_CACHE"]
        }
    try:
        import torch  # type: ignore[import-not-found]

        result["cuda"] = torch.version.cuda
        result["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_properties(0)
            result["gpu"] = {"name": gpu.name, "vram_bytes": gpu.total_memory}
    except ImportError:
        result["torch_error"] = "missing"
    except Exception:
        # Never return upstream exception text: it can contain environment secrets.
        result["torch_error"] = "initialization_failed"
    result["triton_importable"] = False
    if packages["triton-windows"] is not None:
        try:
            from musubi_tuner.modules.convrot_int8_kernels import HAS_TRITON  # type: ignore[import-not-found]

            result["triton_importable"] = bool(HAS_TRITON)
        except Exception:
            # Import checks do not compile kernels or expose upstream exception text.
            result["triton_error"] = "initialization_failed"
    print("MUSUBI_PROBE=" + json.dumps(result))


if __name__ == "__main__":
    main()

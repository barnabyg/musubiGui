"""Read-only probe, executed by the explicitly selected training Python."""

import importlib.metadata
import json
import platform


def main() -> None:
    packages: dict[str, str | None] = {}
    for name in ["torch", "accelerate", "safetensors", "transformers", "av", "musubi-tuner"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result: dict[str, object] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
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
    print("MUSUBI_PROBE=" + json.dumps(result))


if __name__ == "__main__":
    main()

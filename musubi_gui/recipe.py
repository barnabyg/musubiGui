"""What every training recipe shares: the upstream pin, its stages and their environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from musubi_gui.settings import Settings

# Public upstream Git revision; review the allowlist whenever this recipe pin changes.
UPSTREAM = "f8a1b03794a49239a3539015075f5123d6c07d66"  # pragma: allowlist secret
CAPTION = "A red square moves across a blue background."


@dataclass
class Stage:
    name: str
    args: list[str]


def python_args(settings: Settings) -> list[str]:
    return [settings.python, "-X", "utf8", "-B"]


def cli(settings: Settings, script: str, *args: str) -> list[str]:
    return [*python_args(settings), str(Path(settings.checkout) / script), *args]


def reference_environment(cache: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
        HF_HOME=str(cache / "huggingface"),
        HF_HUB_CACHE=str(cache / "huggingface/hub"),
        HUGGINGFACE_HUB_CACHE=str(cache / "huggingface/hub"),
        HF_XET_CACHE=str(cache / "huggingface/xet"),
        HF_ASSETS_CACHE=str(cache / "huggingface/assets"),
        HUGGINGFACE_ASSETS_CACHE=str(cache / "huggingface/assets"),
        TORCH_HOME=str(cache / "torch"),
        TORCH_EXTENSIONS_DIR=str(cache / "torch_extensions"),
        TORCHINDUCTOR_CACHE_DIR=str(cache / "torch_inductor"),
        CUDA_CACHE_PATH=str(cache / "cuda"),
        TRITON_CACHE_DIR=str(cache / "triton"),
        XDG_CACHE_HOME=str(cache / "xdg"),
        TEMP=str(cache / "tmp"),
        TMP=str(cache / "tmp"),
        MUSUBI_REFERENCE_PROBE="1",
    )
    # Deprecated Transformers variables override HF_HOME and emit warnings if inherited.
    for name in ["TRANSFORMERS_CACHE", "PYTORCH_TRANSFORMERS_CACHE", "PYTORCH_PRETRAINED_BERT_CACHE"]:
        env.pop(name, None)
    (cache / "tmp").mkdir(parents=True, exist_ok=True)
    return env

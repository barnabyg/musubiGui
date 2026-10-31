"""Read a safetensors file's header without loading its tensors."""

import json
from pathlib import Path
from typing import Any


def safetensors_header(file: Path) -> dict[str, Any]:
    with file.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        if not 2 <= size <= 32 * 1024**2:
            raise ValueError("Invalid safetensors header length.")
        return dict(json.loads(stream.read(size)))

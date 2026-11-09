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


def complete_safetensors(file: Path) -> dict[str, Any]:
    """Check tensor payload bounds without loading potentially large weights."""
    header = safetensors_header(file)
    with file.open("rb") as stream:
        available = file.stat().st_size - 8 - int.from_bytes(stream.read(8), "little")
    tensors = [value for key, value in header.items() if key != "__metadata__"]
    if not tensors:
        raise ValueError("Checkpoint contains no tensors.")
    for tensor in tensors:
        if not isinstance(tensor, dict):
            raise ValueError("Checkpoint tensor metadata is malformed.")
        offsets = tensor.get("data_offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(isinstance(value, int) for value in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= available
        ):
            raise ValueError("Checkpoint payload is incomplete or has invalid tensor offsets.")
    return header

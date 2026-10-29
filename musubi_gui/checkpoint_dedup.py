"""Store a training result once when upstream's last interval save repeats its final save.

When the step count is a multiple of the checkpoint interval, the pinned upstream writes
`<name>-stepN.safetensors` and `<name>-stepN-state/` at step N, then always writes the final
`<name>.safetensors` and `<name>-state/` too. Only proven duplicates are removed; the final
copies are always retained because sampling and restoration use them.
"""

import hashlib
import json
import shutil
from pathlib import Path

from musubi_gui.snapshots import digest_file


def tensor_digest(file: Path) -> str:
    """Hash tensor layout and data, ignoring the save date, epoch and finish time in `__metadata__`."""
    with file.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        if not 2 <= size <= 32 * 1024**2:
            raise ValueError("Invalid safetensors header length.")
        header = json.loads(stream.read(size))
        header.pop("__metadata__", None)
        hasher = hashlib.sha256(json.dumps(header, sort_keys=True).encode())
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def state_digests(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): digest_file(path) for path in directory.rglob("*") if path.is_file()
    }


def remove_final_duplicates(directory: Path, output_name: str, steps: int) -> list[str]:
    """Remove the step-`steps` checkpoint and state when they match the final save; return removed names."""
    removed = []
    step_name = f"{output_name}-step{steps:08d}"
    copy, final = directory / f"{step_name}.safetensors", directory / f"{output_name}.safetensors"
    if copy.is_file() and final.is_file() and tensor_digest(copy) == tensor_digest(final):
        copy.unlink()
        removed.append(copy.name)
    copy, final = directory / f"{step_name}-state", directory / f"{output_name}-state"
    if copy.is_dir() and final.is_dir():
        files = state_digests(copy)
        if files and files == state_digests(final):
            # Renaming first means a failed removal never leaves a partial state that looks restorable.
            hidden = copy.with_name(f".{copy.name}.duplicate")
            copy.rename(hidden)
            shutil.rmtree(hidden)
            removed.append(copy.name)
    return removed

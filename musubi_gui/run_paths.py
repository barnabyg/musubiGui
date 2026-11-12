"""Readable Windows-safe Run folders; internal IDs remain independent."""

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path


def job_folder(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", unicodedata.normalize("NFC", name))
    value = re.sub(r"\s+", "-", value).strip(" .-")[:60].rstrip(" .-") or "Job"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", value, re.IGNORECASE):
        value = "Job-" + value
    return value


def create_run_directory(root: Path, name: str, kind: str | None = None) -> Path:
    """Atomically reserve a timestamped folder, even for concurrent same-name Runs."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%SZ")
    stem = "-".join(part for part in [job_folder(name), kind, stamp] if part)
    root.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        directory = root / (stem if suffix == 1 else f"{stem}-{suffix}")
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            suffix += 1

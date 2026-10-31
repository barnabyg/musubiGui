"""Helpers shared across test modules."""

from pathlib import Path
from typing import Any

from musubi_gui.acceptance import prepare


def acceptance_workspace(tmp_path: Path) -> dict[str, Any]:
    """A fixture workspace without the mixed-media library scan, which no pytest test reads."""
    return prepare(tmp_path / "acceptance", "fixture", None, scan_library=False)

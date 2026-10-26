"""Versioned, atomic setup persistence and configured-root policy."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    checkout: str = ""
    python: str = ""
    model_roots: list[str] = Field(default_factory=list)
    dataset_roots: list[str] = Field(default_factory=list)
    models: dict[str, str] = Field(default_factory=dict)
    output_root: str = ""
    cache_root: str = ""


def state_root() -> Path:
    return Path(os.environ.get("MUSUBI_GUI_STATE", ".state")).resolve()


def load_settings(root: Path) -> Settings:
    file = root / "settings.json"
    if not file.exists():
        return Settings()
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported settings version; preserve settings.json and restore a compatible application.")
    return Settings.model_validate(data)


def save_settings(root: Path, settings: Settings) -> None:
    if settings.version != 1:
        raise ValueError("Settings version must be 1.")
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "settings.json", settings.model_dump())


def atomic_json(file: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(dir=file.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, file)
    finally:
        Path(name).unlink(missing_ok=True)


def within_root(path: Path, roots: list[Path]) -> Path:
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("Path is outside the configured roots (including symlink/junction resolution).")
    return resolved


def validate_paths(settings: Settings) -> None:
    paths = [
        settings.checkout,
        settings.python,
        settings.output_root,
        settings.cache_root,
        *settings.model_roots,
        *settings.dataset_roots,
        *settings.models.values(),
    ]
    if any(value and (not Path(value).is_absolute() or "\x00" in value) for value in paths):
        raise ValueError("Use absolute local paths for every configured location.")
    for value in paths:
        if value.startswith(("\\\\", "//")) or "://" in value:
            raise ValueError("Network paths and URLs are not supported; select a local path.")
    writes = [Path(value).resolve() for value in [settings.output_root, settings.cache_root] if value]
    if writes:
        protected = [
            Path(p).resolve() for p in [settings.checkout, *settings.model_roots, *settings.dataset_roots] if p
        ]
        for target in writes:
            if target == Path(target.anchor) or any(
                target.is_relative_to(p) or p.is_relative_to(target) for p in protected
            ):
                raise ValueError(
                    "Output/cache roots must be dedicated directories outside the checkout and model roots."
                )
        if len(writes) == 2 and (writes[0].is_relative_to(writes[1]) or writes[1].is_relative_to(writes[0])):
            raise ValueError("Output and cache roots must be separate, non-overlapping directories.")
    for value in settings.models.values():
        if value:
            within_root(Path(value), [Path(p) for p in settings.model_roots])

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from musubi_gui.run_paths import create_run_directory, job_folder


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My training job", "My-training-job"),
        (' ../a:b/c\\d?*|<>" ', "a-b-c-d"),
        ("CON", "Job-CON"),
        ("nul.txt", "Job-nul.txt"),
        ("LPT¹", "Job-LPT¹"),
        ("...", "Job"),
        ("日光", "日光"),
        ("a" * 100, "a" * 60),
    ],
)
def test_job_folder_is_readable_and_windows_safe(name: str, expected: str) -> None:
    assert job_folder(name) == expected


def test_concurrent_run_folders_use_timestamp_and_atomic_collision_suffixes(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    existing = root / "My-Job-2026-11-12_12-30-00Z"
    existing.write_text("preserve existing output", encoding="utf-8")
    with patch("musubi_gui.run_paths.datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 12, 12, 30, tzinfo=UTC)
        with ThreadPoolExecutor(max_workers=8) as pool:
            directories = list(pool.map(lambda _: create_run_directory(root, "My Job"), range(20)))
        retry = create_run_directory(root, "My Job", "retry")
        restore = create_run_directory(root, "My Job", "restore")
    assert len(set(directories)) == 20
    assert {directory.name for directory in directories} == {f"{existing.name}-{n}" for n in range(2, 22)}
    assert all(directory.is_dir() for directory in directories)
    assert existing.read_text(encoding="utf-8") == "preserve existing output"
    assert retry.name == "My-Job-retry-2026-11-12_12-30-00Z"
    assert restore.name == "My-Job-restore-2026-11-12_12-30-00Z"

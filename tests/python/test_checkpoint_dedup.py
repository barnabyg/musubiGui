import json
import shutil
from pathlib import Path

import pytest

from musubi_gui.checkpoint_dedup import remove_final_duplicates


def checkpoint(file: Path, weights: bytes, metadata: dict[str, str]) -> None:
    header = json.dumps(
        {
            "__metadata__": metadata,
            "lora.weight": {"dtype": "U8", "shape": [len(weights)], "data_offsets": [0, len(weights)]},
        }
    ).encode()
    file.write_bytes(len(header).to_bytes(8, "little") + header + weights)


def state(directory: Path, optimizer: bytes) -> None:
    directory.mkdir()
    (directory / "model.safetensors").write_bytes(b"model")
    (directory / "optimizer.bin").write_bytes(optimizer)


def outputs(root: Path, *, step_weights: bytes = b"same", step_optimizer: bytes = b"adam") -> Path:
    # Upstream stamps each save with its own date, epoch and finish time.
    checkpoint(root / "lora-step00000200.safetensors", step_weights, {"ss_epoch": "7", "modelspec.date": "14:33:41"})
    checkpoint(root / "lora.safetensors", b"same", {"ss_epoch": "8", "modelspec.date": "14:33:43"})
    state(root / "lora-step00000200-state", step_optimizer)
    state(root / "lora-state", b"adam")
    return root


def test_step_copies_of_the_final_save_are_removed_when_weights_and_state_match(tmp_path: Path) -> None:
    root = outputs(tmp_path)
    assert remove_final_duplicates(root, "lora", 200) == (
        ["lora-step00000200.safetensors", "lora-step00000200-state"],
        [],
    )
    assert sorted(path.name for path in root.iterdir()) == ["lora-state", "lora.safetensors"]


def test_step_copies_that_differ_from_the_final_save_are_kept(tmp_path: Path) -> None:
    root = outputs(tmp_path, step_weights=b"diff", step_optimizer=b"sgd!")
    assert remove_final_duplicates(root, "lora", 200) == ([], [])
    assert len(list(root.iterdir())) == 4


def test_a_state_with_an_extra_file_is_not_a_duplicate(tmp_path: Path) -> None:
    root = outputs(tmp_path)
    (root / "lora-step00000200-state" / "scaler.pt").write_bytes(b"scaler")
    assert remove_final_duplicates(root, "lora", 200) == (["lora-step00000200.safetensors"], [])
    assert (root / "lora-step00000200-state").is_dir()


def test_a_step_count_without_an_interval_save_removes_nothing(tmp_path: Path) -> None:
    root = outputs(tmp_path)
    assert remove_final_duplicates(root, "lora", 150) == ([], [])
    assert len(list(root.iterdir())) == 4


def test_a_failed_state_removal_still_reports_the_removed_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = outputs(tmp_path)

    def locked(path: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(shutil, "rmtree", locked)
    removed, problems = remove_final_duplicates(root, "lora", 200)
    assert removed == ["lora-step00000200.safetensors"]
    assert len(problems) == 1 and "lora-step00000200-state" in problems[0] and "in use" in problems[0]
    # The partly removed copy can no longer be mistaken for a restorable state.
    assert not (root / "lora-step00000200-state").exists()
    assert ".lora-step00000200-state.duplicate" in problems[0]

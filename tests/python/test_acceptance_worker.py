import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_fake_worker_refuses_to_run_outside_an_explicit_synthetic_checkout(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.acceptance_worker", "train", "--output_dir", str(tmp_path / "outputs")],
        cwd=tmp_path,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 2
    assert "synthetic checkout marker" in result.stdout
    assert not (tmp_path / "outputs").exists()


def test_fake_worker_produces_an_explicit_synthetic_checkpoint_and_resume_metadata(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".musubi-gui-fixture.json").write_text(
        json.dumps({"version": 1, "kind": "synthetic-worker"}), encoding="utf-8"
    )
    output = tmp_path / "outputs"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musubi_gui.acceptance_worker",
            "train",
            "--output_dir",
            str(output),
            "--output_name",
            "fixture",
            "--max_train_steps",
            "2",
            "--save_state",
        ],
        cwd=checkout,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SYNTHETIC" in result.stdout
    assert (output / "fixture.safetensors").is_file()
    state = json.loads((output / "state" / "fixture-state" / "trainer_state.json").read_text(encoding="utf-8"))
    assert state == {"version": 1, "synthetic": True, "global_step": 2, "optimizer_step": 2, "segment_steps": 2}
    assert sorted(path.name for path in (output / "state" / "fixture-state").iterdir()) == [
        "model.safetensors",
        "optimizer.bin",
        "random_states_0.pkl",
        "scheduler.bin",
        "trainer_state.json",
    ]


@pytest.mark.parametrize(("family", "task"), [("wan", "t2v-A14B"), ("h3", "t2va")])
def test_fake_worker_numbers_a_restored_segment_by_global_step(tmp_path: Path, family: str, task: str) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".musubi-gui-fixture.json").write_text(
        json.dumps({"version": 1, "kind": "synthetic-worker"}), encoding="utf-8"
    )
    prompts = tmp_path / "previews.json"
    prompts.write_text(json.dumps([{"seed": 42, "frame_count": 1, "width": 16, "height": 16}]), encoding="utf-8")
    output = tmp_path / "outputs"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musubi_gui.acceptance_worker",
            "train",
            "--task",
            task,
            "--output_dir",
            str(output),
            "--output_name",
            "fixture",
            "--max_train_steps",
            "4",
            "--save_every_n_steps",
            "2",
            "--sample_every_n_steps",
            "2",
            "--sample_prompts",
            str(prompts),
            "--save_state",
        ],
        cwd=checkout,
        # Resumed at 3, so the four local steps are global 4 to 7: interval 2 falls due at 4 and 6.
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "MUSUBI_STEP_OFFSET": "3",
            "MUSUBI_RUN_TIMING_FILE": str(tmp_path / "timing.json"),
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in output.iterdir()) == [
        "fixture-step00000004.safetensors",
        "fixture-step00000006.safetensors",
        "fixture.safetensors",
        "samples",
        "state",
    ]
    assert sorted(path.name for path in (output / "state").iterdir()) == [
        "fixture-state",
        "fixture-step00000004-state",
        "fixture-step00000006-state",
    ]
    assert sorted(path.name.split("_")[1] for path in (output / "samples" / "sample").iterdir()) == ["000004", "000006"]
    # The progress bar counts this segment's updates, as upstream's does.
    assert "steps: 4/4" in result.stdout
    timing = json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))
    assert (timing["training"]["step"], timing["previews"]["completed"]) == (7, 2)


def test_fake_worker_resumes_with_the_configured_learning_rate(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".musubi-gui-fixture.json").write_text(
        json.dumps({"version": 1, "kind": "synthetic-worker"}), encoding="utf-8"
    )

    def train(name: str, learning_rate: str, resume: Path | None = None) -> str:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "musubi_gui.acceptance_worker",
                "train",
                "--output_dir",
                str(tmp_path / name),
                "--output_name",
                "fixture",
                "--save_state",
                "--learning_rate",
                learning_rate,
                *(["--resume", str(resume)] if resume else []),
            ],
            cwd=checkout,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def saved_rate(name: str) -> float:
        state = tmp_path / name / "state" / "fixture-state" / "trainer_state.json"
        return float(json.loads(state.read_text(encoding="utf-8"))["learning_rate"])

    assert "Resume learning rate" not in train("first", "1e-4")
    assert saved_rate("first") == 1e-4
    # As the trainer entry point does after the state loads: the configured rate replaces the saved one.
    changed = train("changed", "2e-4", tmp_path / "first" / "state" / "fixture-state")
    assert "Resume learning rate: applied 0.0002; the saved state had 0.0001." in changed
    assert saved_rate("changed") == 2e-4
    unchanged = train("unchanged", "2e-4", tmp_path / "changed" / "state" / "fixture-state")
    assert "Resume learning rate: 0.0002, unchanged from the saved state." in unchanged

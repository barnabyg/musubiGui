import json
import subprocess
import sys
from pathlib import Path


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
    state = json.loads((output / "fixture-state" / "trainer_state.json").read_text(encoding="utf-8"))
    assert state == {"version": 1, "synthetic": True, "global_step": 2, "optimizer_step": 2, "segment_steps": 2}
    assert sorted(path.name for path in (output / "fixture-state").iterdir()) == [
        "model.safetensors",
        "optimizer.bin",
        "random_states_0.pkl",
        "scheduler.bin",
        "trainer_state.json",
    ]

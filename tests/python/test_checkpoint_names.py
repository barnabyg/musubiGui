"""Save-boundary coverage for the app-owned adapter around the pinned trainer."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from musubi_gui.checkpoint_names import training_saves


def synthetic_trainer(tmp_path: Path, family: str) -> Path:
    """A stand-in for the pinned trainer using its get_step_ckpt_name and state-name contracts.

    Like the pinned loop, each interval check follows the post-optimizer hook and reads the current interval.
    """
    module, trainer = (
        ("wan_train_network", "WanNetworkTrainer")
        if family == "wan"
        else ("minimax_h3_train_network", "MiniMaxH3NetworkTrainer")
    )
    package = tmp_path / "musubi_tuner"
    utils = package / "utils"
    utils.mkdir(parents=True)
    (package / "__init__.py").touch()
    (utils / "__init__.py").touch()
    (utils / "train_utils.py").write_text(
        "from pathlib import Path\n"
        'STEP_FILE_NAME = "{}-step{:08d}"\nSTEP_STATE_NAME = "{}-step{:08d}-state"\n'
        'def get_step_ckpt_name(name, step): return STEP_FILE_NAME.format(name, step) + ".safetensors"\n'
        'def get_last_ckpt_name(name): return name + ".safetensors"\n'
        "def save_and_remove_state_stepwise(args, step):\n"
        " (Path(args.output_dir) / STEP_STATE_NAME.format(args.output_name, step)).mkdir(parents=True)\n"
        "def save_and_remove_state_on_epoch_end(*args): pass\n"
        "def save_state_on_train_end(args):\n"
        " (Path(args.output_dir) / (args.output_name+'-state')).mkdir(parents=True)\n",
        encoding="utf-8",
    )
    (package / f"{module}.py").write_text(
        "import argparse, json\nfrom pathlib import Path\nfrom musubi_tuner.utils import train_utils\n"
        f"class {trainer}:\n"
        " def on_before_sample_images(self,*args): pass\n"
        " def on_after_sample_images(self,*args): pass\n"
        " def sample_images(self,*args): pass\n"
        " def on_post_optimizer_step(self,*args): pass\n"
        "def main():\n"
        " parser=argparse.ArgumentParser()\n"
        " parser.add_argument('--output_name')\n"
        " parser.add_argument('--max_train_steps',type=int)\n"
        " parser.add_argument('--save_every_n_steps',type=int)\n"
        f" args=parser.parse_args()\n args.output_dir='.'\n trainer={trainer}()\n saves=[]\n"
        " def save(name,state,step):\n"
        "  (Path(args.output_dir) / name).write_text(str(step))\n  saves.append((name,state,step))\n"
        " for step in range(args.max_train_steps):\n"
        "  trainer.on_post_optimizer_step(args,None,None,None,False,step)\n"
        "  trainer.on_post_optimizer_step(args,None,None,None,True,step)\n"
        "  if args.save_every_n_steps and (step+1)%args.save_every_n_steps==0:\n"
        "   train_utils.save_and_remove_state_stepwise(args,step+1)\n"
        "   save(train_utils.get_step_ckpt_name(args.output_name,step+1),\n"
        "        train_utils.STEP_STATE_NAME.format(args.output_name,step+1),step+1)\n"
        " train_utils.save_state_on_train_end(args)\n"
        " save(train_utils.get_last_ckpt_name(args.output_name),args.output_name+'-state',args.max_train_steps)\n"
        " Path('saves.json').write_text(json.dumps(saves))\n",
        encoding="utf-8",
    )
    upstream = tmp_path / f"{module}.py"
    upstream.write_text(f"from musubi_tuner.{module} import main\nmain()\n", encoding="utf-8")
    return upstream


def run_entry_point(
    tmp_path: Path, family: str, output_name: str, steps: int, save_every: int, environment: dict[str, str]
) -> list[list[Any]]:
    """Run the real entry point, not the CPU worker, around the stand-in trainer; returns its saves."""
    upstream = synthetic_trainer(tmp_path, family)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "musubi_gui/timed_trainer.py"),
            "--timing-file",
            str(tmp_path / "timing.json"),
            "--family",
            family,
            "--upstream",
            str(upstream),
            "--output_name",
            output_name,
            "--max_train_steps",
            str(steps),
            "--save_every_n_steps",
            str(save_every),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path), **environment},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    saves: list[list[Any]] = json.loads((tmp_path / "saves.json").read_text())
    for checkpoint, state, _ in saves:
        assert (tmp_path / checkpoint).is_file()
        assert (tmp_path / "state" / state).is_dir()
    return saves


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_native_entry_point_names_restored_segment_saves_by_global_step(tmp_path: Path, family: str) -> None:
    # Resumed at 250 with saves every 100: local steps 50, 150 and 250 are global 300, 400 and 500.
    saves = run_entry_point(tmp_path, family, "Job-r1", 250, 100, {"MUSUBI_STEP_OFFSET": "250"})
    assert saves == [
        *[
            [f"Job-r1-step{step:08d}.safetensors", f"Job-r1-step{step:08d}-state", step - 250]
            for step in (300, 400, 500)
        ],
        ["Job-r1.safetensors", "Job-r1-state", 250],
    ]
    assert json.loads((tmp_path / "timing.json").read_text())["training"]["step"] == 500


def test_expert_naming_takes_precedence_over_a_general_offset(tmp_path: Path) -> None:
    saves = run_entry_point(
        tmp_path,
        "wan",
        "Job-r1-low-step00000104",
        4,
        2,
        {"MUSUBI_EXPERT_STEP_OFFSET": "100", "MUSUBI_STEP_OFFSET": "250"},
    )
    assert [name for name, _, _ in saves] == [
        "Job-r1-low-step00000102.safetensors",
        "Job-r1-low-step00000104.safetensors",
    ]


@pytest.mark.parametrize("expert,offset", [("low", 0), ("high", 0), ("low", 100), ("high", 137)])
def test_native_entry_point_names_interval_and_final_saves_after_completed_expert_steps(
    tmp_path: Path, expert: str, offset: int
) -> None:
    final = f"Job-step-test-r1-{expert}-step{offset + 4:08d}"
    saves = run_entry_point(tmp_path, "wan", final, 4, 2, {"MUSUBI_EXPERT_STEP_OFFSET": str(offset)})
    assert saves == [
        [
            f"Job-step-test-r1-{expert}-step{offset + step:08d}.safetensors",
            f"Job-step-test-r1-{expert}-step{offset + step:08d}-state",
            step,
        ]
        for step in (2, 4)
    ]
    for name, _, step in saves:
        assert (tmp_path / name).read_text() == str(step)


@pytest.mark.parametrize("suffix", [".safetensors", "-state"])
@pytest.mark.parametrize("legacy", [False, True])
def test_discovery_preserves_legacy_local_steps_and_excludes_other_expert_phases(
    tmp_path: Path, suffix: str, legacy: bool
) -> None:
    name = "Job-r1-low-phase00000200" if legacy else "Job-r1-low-step00000200"
    stage = {
        "args": ["--output_name", name, "--max_train_steps", "100"],
        "training": {} if legacy else {"checkpoint_step_offset": 100},
    }
    expected = [
        (50, name + "-step00000050" if legacy else "Job-r1-low-step00000150"),
        (100, name),
    ]
    for stem in [
        *[stem for _, stem in expected],
        "Job-r1-low-step00000100",
        "Job-r1-low-step00000250",
        "Job-r1-high-step00000150",
    ]:
        (tmp_path / (stem + suffix)).touch()
    assert [(step, path.name) for step, path in training_saves(stage, tmp_path, suffix)] == [
        (step, stem + suffix) for step, stem in expected
    ]


@pytest.mark.parametrize("suffix", [".safetensors", "-state"])
def test_discovery_reads_restored_segment_saves_named_by_global_step(tmp_path: Path, suffix: str) -> None:
    stage = {"args": ["--output_name", "Job-r1", "--max_train_steps", "200"]}
    for stem in ["Job-r1-step00000400", "Job-r1", "Job-r1-step00000300", "Job-r1-step00000600"]:
        (tmp_path / (stem + suffix)).touch()
    # Resumed at 300: the save at global 400 is local step 100, the final save local 200.
    assert [(step, path.name) for step, path in training_saves(stage, tmp_path, suffix, 300)] == [
        (100, "Job-r1-step00000400" + suffix),
        (200, "Job-r1" + suffix),
    ]

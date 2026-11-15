"""Save-boundary coverage for the app-owned adapter around the pinned trainer."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from musubi_gui.checkpoint_names import training_saves


@pytest.mark.parametrize("expert,offset", [("low", 0), ("high", 0), ("low", 100), ("high", 137)])
def test_native_entry_point_names_interval_and_final_saves_after_completed_expert_steps(
    tmp_path: Path, expert: str, offset: int
) -> None:
    # Synthetic external trainer uses the pinned get_step_ckpt_name and state-name
    # contracts. The subprocess runs the real entry point, not the CPU worker.
    package = tmp_path / "musubi_tuner"
    utils = package / "utils"
    utils.mkdir(parents=True)
    (package / "__init__.py").touch()
    (utils / "__init__.py").touch()
    (utils / "train_utils.py").write_text(
        'STEP_FILE_NAME = "{}-step{:08d}"\nSTEP_STATE_NAME = "{}-step{:08d}-state"\n'
        'def get_step_ckpt_name(name, step): return STEP_FILE_NAME.format(name, step) + ".safetensors"\n'
        'def get_last_ckpt_name(name): return name + ".safetensors"\n',
        encoding="utf-8",
    )
    (package / "wan_train_network.py").write_text(
        "import argparse, json\nfrom pathlib import Path\nfrom musubi_tuner.utils import train_utils\n"
        "class WanNetworkTrainer:\n"
        " def on_before_sample_images(self,*args): pass\n"
        " def on_after_sample_images(self,*args): pass\n"
        " def sample_images(self,*args): pass\n"
        " def on_post_optimizer_step(self,*args): pass\n"
        "def main():\n"
        " parser=argparse.ArgumentParser()\n"
        " parser.add_argument('--output_name')\n"
        " parser.add_argument('--max_train_steps',type=int)\n"
        " parser.add_argument('--save_every_n_steps',type=int)\n"
        " args=parser.parse_args()\n trainer=WanNetworkTrainer()\n saves=[]\n"
        " def save(name,state,step):\n"
        "  Path(name).write_text(str(step))\n  Path(state).mkdir()\n  saves.append((name,state,step))\n"
        " for step in range(args.max_train_steps):\n"
        "  trainer.on_post_optimizer_step(args,None,None,None,False,step)\n"
        "  trainer.on_post_optimizer_step(args,None,None,None,True,step)\n"
        "  if args.save_every_n_steps and (step+1)%args.save_every_n_steps==0:\n"
        "   save(train_utils.get_step_ckpt_name(args.output_name,step+1),\n"
        "        train_utils.STEP_STATE_NAME.format(args.output_name,step+1),step+1)\n"
        " save(train_utils.get_last_ckpt_name(args.output_name),args.output_name+'-state',args.max_train_steps)\n"
        " Path('saves.json').write_text(json.dumps(saves))\n",
        encoding="utf-8",
    )
    upstream = tmp_path / "wan_train_network.py"
    upstream.write_text("from musubi_tuner.wan_train_network import main\nmain()\n", encoding="utf-8")
    final = f"Job-step-test-r1-{expert}-step{offset + 4:08d}"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "musubi_gui/timed_trainer.py"),
            "--timing-file",
            str(tmp_path / "timing.json"),
            "--family",
            "wan",
            "--upstream",
            str(upstream),
            "--output_name",
            final,
            "--max_train_steps",
            "4",
            "--save_every_n_steps",
            "2",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path), "MUSUBI_EXPERT_STEP_OFFSET": str(offset)},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    saves = json.loads((tmp_path / "saves.json").read_text())
    assert saves == [
        [
            f"Job-step-test-r1-{expert}-step{offset + step:08d}.safetensors",
            f"Job-step-test-r1-{expert}-step{offset + step:08d}-state",
            step,
        ]
        for step in (2, 4)
    ]
    for name, state, step in saves:
        assert (tmp_path / name).read_text() == str(step)
        assert (tmp_path / state).is_dir()


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

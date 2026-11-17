"""Continuous step numbering for restored segments, checked against a stand-in for the pinned training loop."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from musubi_gui.step_offset import OffsetStepName, StepSchedule, due_steps, segment_step_offset
from musubi_gui.timed_trainer import observe_trainer
from musubi_gui.training_timing import TrainingTiming

STEP_FILE_NAME = "{}-step{:08d}"
STEP_STATE_NAME = "{}-step{:08d}-state"


def should_sample_images(args: Any, steps: int, epoch: int | None = None) -> bool:
    """The pinned `sampling_prompts.should_sample_images`, for step-based previews."""
    if steps == 0:
        return bool(args.sample_at_first)
    return args.sample_every_n_steps is not None and steps % args.sample_every_n_steps == 0


class Trainer:
    """The pinned `trainer_base` hook order: post-optimizer hook, increment, then the save and sample checks."""

    def __init__(self) -> None:
        self.saves: list[str] = []
        self.samples: list[tuple[int, int]] = []

    def on_before_sample_images(self, *args: Any) -> None:
        pass

    def on_after_sample_images(self, *args: Any) -> None:
        pass

    def on_post_optimizer_step(self, *args: Any) -> None:
        pass

    def sample_images(self, accelerator: Any, args: Any, epoch: Any, steps: int, *rest: Any) -> None:
        if not should_sample_images(args, steps, epoch):
            return
        sample = Path(args.output_dir) / "sample"
        sample.mkdir(exist_ok=True)
        (sample / f"{args.output_name}_{steps:06d}_00_20261110000000_42_000.png").write_bytes(b"png")
        self.samples.append((steps, args.sample_every_n_steps))

    def train(self, args: Any, names: SimpleNamespace) -> None:
        def do_sample(epoch: Any, steps: int) -> None:
            if not should_sample_images(args, steps, epoch):
                return
            self.on_before_sample_images(None, args, epoch, steps)
            try:
                self.sample_images(None, args, epoch, steps)
            finally:
                self.on_after_sample_images(None, args, epoch, steps)

        global_step = 0
        for _ in range(args.max_train_steps):
            # Gradient accumulation calls the hook without a synchronised update first.
            self.on_post_optimizer_step(args, None, None, None, False, global_step)
            self.on_post_optimizer_step(args, None, None, None, True, global_step)
            global_step += 1
            if should_sample_images(args, global_step):
                do_sample(None, global_step)
            if args.save_every_n_steps is not None and global_step % args.save_every_n_steps == 0:
                self.saves.append(names.STEP_FILE_NAME.format(args.output_name, global_step))
                self.saves.append(names.STEP_STATE_NAME.format(args.output_name, global_step))
        self.saves.append(args.output_name)


def run(tmp_path: Path, offset: int, steps: int, save_every: int, sample_every: int) -> tuple[Trainer, dict[str, Any]]:
    class Observed(Trainer):
        pass

    observe_trainer(Observed, TrainingTiming(tmp_path / "timing.json"), step_offset=offset)
    trainer = Observed()
    args = SimpleNamespace(
        output_dir=str(tmp_path),
        output_name="Job",
        max_train_steps=steps,
        save_every_n_steps=save_every,
        sample_every_n_steps=sample_every,
        sample_at_first=False,
    )
    names = SimpleNamespace(
        STEP_FILE_NAME=OffsetStepName(STEP_FILE_NAME, offset) if offset else STEP_FILE_NAME,
        STEP_STATE_NAME=OffsetStepName(STEP_STATE_NAME, offset) if offset else STEP_STATE_NAME,
    )
    trainer.train(args, names)
    return trainer, json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))


def test_offset_not_a_multiple_of_the_interval_saves_and_previews_at_global_multiples(tmp_path: Path) -> None:
    trainer, timing = run(tmp_path, offset=250, steps=250, save_every=100, sample_every=75)
    assert trainer.saves == [
        "Job-step00000300",
        "Job-step00000300-state",
        "Job-step00000400",
        "Job-step00000400-state",
        "Job-step00000500",
        "Job-step00000500-state",
        "Job",
    ]
    # Upstream's own recheck inside sample_images passes for the global step it names the file with.
    assert [step for step, _ in trainer.samples] == [300, 375, 450]
    assert sorted(file.name.split("_")[1] for file in (tmp_path / "sample").iterdir()) == ["000300", "000375", "000450"]
    assert timing["training"]["step"] == 500
    assert (timing["previews"]["completed"], timing["previews"]["failed"]) == (3, 0)


def test_offset_on_an_interval_multiple_continues_the_cadence(tmp_path: Path) -> None:
    trainer, timing = run(tmp_path, offset=300, steps=200, save_every=100, sample_every=100)
    assert trainer.saves[::2] == ["Job-step00000400", "Job-step00000500", "Job"]
    assert [step for step, _ in trainer.samples] == [400, 500]
    assert timing["training"]["step"] == 500


def test_without_an_offset_the_trainer_behaves_as_upstream(tmp_path: Path) -> None:
    trainer, timing = run(tmp_path, offset=0, steps=4, save_every=2, sample_every=3)
    assert trainer.saves[::2] == ["Job-step00000002", "Job-step00000004", "Job"]
    assert trainer.samples == [(3, 3)]
    assert timing["training"]["step"] == 4


def test_schedule_disables_intervals_between_due_steps() -> None:
    schedule = StepSchedule(7)
    args = SimpleNamespace(save_every_n_steps=5, sample_every_n_steps=None)
    fired = []
    for local in range(1, 10):
        schedule.before_step(args, local)
        assert args.sample_every_n_steps is None
        if args.save_every_n_steps is not None and local % args.save_every_n_steps == 0:
            fired.append(schedule.global_step(local))
    assert fired == [10, 15]


@pytest.mark.parametrize(
    ("interval", "offset", "steps", "expected"),
    [(100, 250, 250, [300, 400, 500]), (100, 0, 250, [100, 200]), (2, 3, 4, [4, 6]), (0, 3, 4, []), (5, 5, 4, [])],
)
def test_due_steps(interval: int, offset: int, steps: int, expected: list[int]) -> None:
    assert due_steps(interval, offset, steps) == expected


def test_offset_applies_to_plain_training_stages_of_restored_segments_only() -> None:
    restored = {"segment": {"step_offset": 300, "target_steps": 500}}
    assert segment_step_offset(restored, {"name": "train"}) == 300
    assert segment_step_offset(restored, {"name": "generate"}) == 0
    # Alternating Wan phases keep their per-expert names.
    assert segment_step_offset(restored, {"name": "train", "training": {"expert": "low"}}) == 0
    # Segments restored before continuous numbering recorded only their additional steps.
    assert segment_step_offset({"segment": {"additional_steps": 200}}, {"name": "train"}) == 0
    assert segment_step_offset({}, {"name": "train"}) == 0

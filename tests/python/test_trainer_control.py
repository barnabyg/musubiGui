"""Save now, Sample now and Save and stop, checked against a stand-in for the pinned training loop."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from musubi_gui.step_offset import OffsetStepName
from musubi_gui.timed_trainer import observe_trainer, route_saved_states
from musubi_gui.trainer_control import HANDLED_FILE, REQUEST_FILE, StopAfterSave, TrainerControl, write_json
from musubi_gui.training_timing import TrainingTiming

STEP_FILE_NAME = "{}-step{:08d}"
STEP_STATE_NAME = "{}-step{:08d}-state"


def should_sample_images(args: Any, steps: int, epoch: int | None = None) -> bool:
    return args.sample_every_n_steps is not None and steps % args.sample_every_n_steps == 0


class Trainer:
    """The pinned `trainer_base` loop: batch, post-optimizer hook, increment, sample and save checks, epoch end."""

    def __init__(self, requests: dict[int, str], folder: Path, epoch_steps: int) -> None:
        # Local step at whose batch the backend writes a request, as if between two polls.
        self.requests = requests
        self.folder = folder
        self.epoch_steps = epoch_steps
        self.saves: list[str] = []
        self.samples: list[int] = []
        self.batches = 0

    def on_before_sample_images(self, *args: Any) -> None:
        pass

    def on_after_sample_images(self, *args: Any) -> None:
        pass

    def on_post_optimizer_step(self, *args: Any) -> None:
        pass

    def on_epoch_end(self, *args: Any) -> None:
        pass

    def process_batch(self, *args: Any) -> None:
        self.batches += 1
        if action := self.requests.get(self.batches):
            write_json(self.folder / REQUEST_FILE, {"id": f"request-{self.batches}", "action": action})

    def sample_images(self, accelerator: Any, args: Any, epoch: Any, steps: int, *rest: Any) -> None:
        if should_sample_images(args, steps, epoch):
            self.samples.append(steps)
            sample = Path(args.output_dir) / "sample" / f"Job_{steps:06d}_00.mp4"
            sample.parent.mkdir(parents=True, exist_ok=True)
            sample.write_bytes(b"preview")

    def train(self, args: Any, names: SimpleNamespace) -> None:
        global_step = 0
        epochs = -(-args.max_train_steps // self.epoch_steps)
        for epoch in range(epochs):
            for _ in range(self.epoch_steps):
                self.process_batch(args, global_step)
                self.on_post_optimizer_step(args, None, None, None, True, global_step)
                global_step += 1
                if should_sample_images(args, global_step):
                    self.on_before_sample_images(None, args, None, global_step)
                    self.sample_images(None, args, None, global_step)
                    self.on_after_sample_images(None, args, None, global_step)
                if args.save_every_n_steps is not None and global_step % args.save_every_n_steps == 0:
                    self.saves.append(names.STEP_FILE_NAME.format(args.output_name, global_step))
                    if args.save_state:
                        self.saves.append(names.STEP_STATE_NAME.format(args.output_name, global_step))
                if global_step >= args.max_train_steps:
                    break
            self.on_epoch_end(args, None, None, None, epoch + 1)
        if args.save_state or args.save_state_on_train_end:
            self.saves.append(args.output_name + "-state")
        self.saves.append(args.output_name)


def run(
    tmp_path: Path,
    requests: dict[int, str],
    *,
    steps: int = 6,
    save_every: int = 4,
    sample_every: int | None = None,
    save_state: bool = True,
    offset: int = 0,
    epoch_steps: int = 100,
) -> tuple[Trainer, list[dict[str, Any]]]:
    class Observed(Trainer):
        pass

    handled: list[dict[str, Any]] = []

    class Recorded(TrainerControl):
        """Collects each outcome as the backend does between steps."""

        def at_step(self, namespace: Any, local_step: int, step: int) -> None:
            super().at_step(namespace, local_step, step)
            if (tmp_path / HANDLED_FILE).is_file():
                handled.append(json.loads((tmp_path / HANDLED_FILE).read_text(encoding="utf-8")))
                (tmp_path / HANDLED_FILE).unlink()

    control = Recorded(tmp_path / REQUEST_FILE)
    observe_trainer(Observed, TrainingTiming(tmp_path / "timing.json"), step_offset=offset, control=control)
    trainer = Observed(requests, tmp_path, epoch_steps)
    args = SimpleNamespace(
        output_dir=str(tmp_path),
        output_name="Job",
        max_train_steps=steps,
        save_every_n_steps=save_every,
        sample_every_n_steps=sample_every,
        sample_prompts="prompts.json" if sample_every else None,
        sample_at_first=False,
        save_state=save_state,
        save_state_on_train_end=save_state,
    )
    names = SimpleNamespace(
        STEP_FILE_NAME=OffsetStepName(STEP_FILE_NAME, offset),
        STEP_STATE_NAME=OffsetStepName(STEP_STATE_NAME, offset),
    )
    try:
        trainer.train(args, names)
    except StopAfterSave as stop:
        assert stop.code == 0
        trainer.saves.append("stopped")
    return trainer, handled


def test_save_now_saves_the_current_step_and_keeps_the_interval(tmp_path: Path) -> None:
    trainer, handled = run(tmp_path, {3: "save"})
    assert trainer.saves == [
        "Job-step00000003",
        "Job-step00000003-state",
        "Job-step00000004",
        "Job-step00000004-state",
        "Job-state",
        "Job",
    ]
    assert handled == [{"id": "request-3", "action": "save", "step": 3}]
    assert not (tmp_path / REQUEST_FILE).exists()


def test_save_now_without_saved_state_saves_only_the_checkpoint(tmp_path: Path) -> None:
    trainer, _ = run(tmp_path, {1: "save"}, save_state=False)
    assert trainer.saves == ["Job-step00000001", "Job-step00000004", "Job"]


def test_sample_now_previews_the_current_step(tmp_path: Path) -> None:
    trainer, handled = run(tmp_path, {2: "sample"}, sample_every=5)
    assert trainer.samples == [2, 5]
    assert handled[0]["step"] == 2 and "error" not in handled[0]
    assert sorted(file.name for file in (tmp_path / "samples" / "sample").iterdir()) == [
        "Job_000002_00.mp4",
        "Job_000005_00.mp4",
    ]
    assert not (tmp_path / "sample").exists()


def test_state_saves_use_the_state_folder_and_restore_the_checkpoint_destination(tmp_path: Path) -> None:
    def save(args: Any, name: str) -> None:
        destination = Path(args.output_dir) / f"{name}-state"
        destination.mkdir(parents=True)
        if name == "failed":
            raise OSError("save failed")

    helpers = SimpleNamespace(
        save_and_remove_state_stepwise=save,
        save_and_remove_state_on_epoch_end=save,
        save_state_on_train_end=save,
    )
    route_saved_states(helpers)
    args = SimpleNamespace(output_dir=str(tmp_path))
    helpers.save_and_remove_state_stepwise(args, "interval")
    helpers.save_and_remove_state_on_epoch_end(args, "epoch")
    helpers.save_state_on_train_end(args, "final")
    with pytest.raises(OSError, match="save failed"):
        helpers.save_state_on_train_end(args, "failed")
    assert args.output_dir == str(tmp_path)
    assert sorted(folder.name for folder in tmp_path.iterdir()) == ["state"]
    assert sorted(folder.name for folder in (tmp_path / "state").iterdir()) == [
        "epoch-state",
        "failed-state",
        "final-state",
        "interval-state",
    ]


def test_sample_now_is_refused_without_preview_prompts(tmp_path: Path) -> None:
    trainer, handled = run(tmp_path, {2: "sample"})
    assert trainer.samples == []
    assert handled[0]["error"] == "Previews are off for this training process."


def test_save_and_stop_saves_state_and_exits_at_the_next_batch(tmp_path: Path) -> None:
    # The Job doesn't keep state; Save and stop still saves the state a later segment resumes from.
    trainer, handled = run(tmp_path, {2: "save_and_stop"}, save_state=False)
    assert trainer.saves == ["Job-step00000002", "Job-step00000002-state", "stopped"]
    # The next batch never starts.
    assert trainer.batches == 2
    assert handled == [{"id": "request-2", "action": "save_and_stop", "step": 2}]


def test_save_and_stop_at_the_end_of_an_epoch_exits_at_its_end_hook(tmp_path: Path) -> None:
    trainer, _ = run(tmp_path, {2: "save_and_stop"}, epoch_steps=2)
    assert trainer.saves == ["Job-step00000002", "Job-step00000002-state", "stopped"]
    assert trainer.batches == 2


def test_save_and_stop_at_the_final_step_leaves_it_to_the_train_end_save(tmp_path: Path) -> None:
    trainer, handled = run(tmp_path, {6: "save_and_stop"}, save_state=False, save_every=5)
    assert trainer.saves == ["Job-step00000005", "Job-state", "Job"]
    assert handled[0]["step"] == 6


def test_restored_segment_names_extra_saves_by_global_step(tmp_path: Path) -> None:
    trainer, handled = run(tmp_path, {2: "save"}, offset=250, save_every=100, steps=100)
    assert trainer.saves[:4] == [
        "Job-step00000252",
        "Job-step00000252-state",
        "Job-step00000300",
        "Job-step00000300-state",
    ]
    assert handled[0]["step"] == 252


@pytest.mark.parametrize("content", ["{not json", "[]"])
def test_an_unreadable_request_waits_for_a_complete_one(tmp_path: Path, content: str) -> None:
    (tmp_path / REQUEST_FILE).write_text(content, encoding="utf-8")
    control = TrainerControl(tmp_path / REQUEST_FILE)
    args = SimpleNamespace(max_train_steps=4, save_every_n_steps=None)
    control.at_step(args, 1, 1)
    assert args.save_every_n_steps is None
    assert not (tmp_path / HANDLED_FILE).exists()


def test_unknown_action_is_reported_and_removed(tmp_path: Path) -> None:
    write_json(tmp_path / REQUEST_FILE, {"id": "x", "action": "explode"})
    control = TrainerControl(tmp_path / REQUEST_FILE)
    args = SimpleNamespace(max_train_steps=4, save_every_n_steps=None)
    control.at_step(args, 1, 1)
    assert args.save_every_n_steps is None
    assert json.loads((tmp_path / HANDLED_FILE).read_text(encoding="utf-8"))["error"].startswith("Unknown")
    assert not (tmp_path / REQUEST_FILE).exists()
    control.after_save()

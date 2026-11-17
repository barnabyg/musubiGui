"""A resumed segment trains with the configured learning rate, not the one restored with the saved state."""

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from musubi_gui.resume_learning_rate import LearningRateOverride
from musubi_gui.timed_trainer import observe_trainer
from musubi_gui.training_timing import TrainingTiming

LORA_PLUS_RATIO = 16.0


class Optimizer:
    """Torch's param groups: built with each group's `lr`; loading a state replaces the group dicts."""

    def __init__(self, rates: list[float]) -> None:
        self.param_groups = [{"params": [index], "lr": rate} for index, rate in enumerate(rates)]

    def state_dict(self) -> dict[str, Any]:
        return {
            "param_groups": copy.deepcopy([{k: v for k, v in g.items() if k != "params"} for g in self.param_groups])
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.param_groups = [
            {**saved, "params": group["params"]} for group, saved in zip(self.param_groups, state["param_groups"])
        ]


class Scheduler:
    """A LambdaLR: records each group's `initial_lr` as its base and sets `lr` to base times the factor."""

    def __init__(self, optimizer: Optimizer, factor: float = 1.0) -> None:
        self.optimizer = optimizer
        self.factor = factor
        for group in optimizer.param_groups:
            group.setdefault("initial_lr", group["lr"])
        self.base_lrs = [group["initial_lr"] for group in optimizer.param_groups]
        self.step()

    def step(self) -> None:
        for group, base in zip(self.optimizer.param_groups, self.base_lrs):
            group["lr"] = base * self.factor

    def state_dict(self) -> dict[str, Any]:
        return {"base_lrs": list(self.base_lrs), "factor": self.factor}

    def load_state_dict(self, state: dict[str, Any]) -> None:
        self.__dict__.update(copy.deepcopy(state))


def saved_state(rates: list[float], factor: float = 1.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """The optimizer and scheduler state a Run trained with `rates` saved."""
    optimizer = Optimizer(rates)
    scheduler = Scheduler(optimizer, factor)
    return optimizer.state_dict(), scheduler.state_dict()


def test_a_changed_learning_rate_replaces_the_restored_one() -> None:
    optimizer = Optimizer([1e-4])
    scheduler = Scheduler(optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, scheduler)
    saved_optimizer, saved_scheduler = saved_state([5e-5])
    optimizer.load_state_dict(saved_optimizer)
    scheduler.load_state_dict(saved_scheduler)
    assert optimizer.param_groups[0]["lr"] == 5e-5

    line = override.apply(resuming=True, learning_rate=1e-4, optimizer=optimizer)

    assert line == "Resume learning rate: applied 0.0001; the saved state had 5e-05."
    assert optimizer.param_groups == [{"params": [0], "lr": 1e-4, "initial_lr": 1e-4}]
    assert scheduler.base_lrs == [1e-4]
    # The scheduler's next step keeps the applied rate.
    scheduler.step()
    assert optimizer.param_groups[0]["lr"] == 1e-4


def test_a_scaled_param_group_keeps_its_ratio_to_the_configured_rate() -> None:
    # LoRA+ builds a second group for `lora_up` at a multiple of the learning rate.
    optimizer = Optimizer([2e-4, 2e-4 * LORA_PLUS_RATIO])
    scheduler = Scheduler(optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, scheduler)
    saved_optimizer, saved_scheduler = saved_state([1e-4, 1e-4 * LORA_PLUS_RATIO])
    optimizer.load_state_dict(saved_optimizer)
    scheduler.load_state_dict(saved_scheduler)

    line = override.apply(resuming=True, learning_rate=2e-4, optimizer=optimizer)

    assert [group["lr"] for group in optimizer.param_groups] == [2e-4, 2e-4 * LORA_PLUS_RATIO]
    assert [group["initial_lr"] for group in optimizer.param_groups] == [2e-4, 2e-4 * LORA_PLUS_RATIO]
    assert scheduler.base_lrs == [2e-4, 2e-4 * LORA_PLUS_RATIO]
    assert line == (
        "Resume learning rate: applied 0.0002; the saved state had 0.0001. "
        "Parameter groups: 0.0002, 0.0032 (saved 0.0001, 0.0016)."
    )


def test_the_scheduler_factor_at_the_saved_step_is_kept() -> None:
    # Saved mid-warmup at half the base rate: the next update uses half the new base rate.
    optimizer = Optimizer([4e-4])
    scheduler = Scheduler(optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, scheduler)
    saved_optimizer, saved_scheduler = saved_state([1e-4], factor=0.5)
    optimizer.load_state_dict(saved_optimizer)
    scheduler.load_state_dict(saved_scheduler)

    override.apply(resuming=True, learning_rate=4e-4, optimizer=optimizer)

    assert optimizer.param_groups[0]["lr"] == 2e-4
    assert (optimizer.param_groups[0]["initial_lr"], scheduler.base_lrs) == (4e-4, [4e-4])


def test_an_unchanged_learning_rate_changes_nothing() -> None:
    optimizer = Optimizer([1e-4])
    scheduler = Scheduler(optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, scheduler)
    saved_optimizer, saved_scheduler = saved_state([1e-4], factor=0.5)
    optimizer.load_state_dict(saved_optimizer)
    scheduler.load_state_dict(saved_scheduler)
    restored = copy.deepcopy(optimizer.param_groups)

    line = override.apply(resuming=True, learning_rate=1e-4, optimizer=optimizer)

    assert line == "Resume learning rate: 0.0001, unchanged from the saved state."
    assert optimizer.param_groups == restored
    assert scheduler.base_lrs == [1e-4]


def test_nothing_is_applied_without_resuming() -> None:
    optimizer = Optimizer([1e-4])
    scheduler = Scheduler(optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, scheduler)
    # Even if the groups somehow differ, a fresh Run's optimizer is the configuration.
    optimizer.param_groups[0]["lr"] = 3e-4
    assert override.apply(resuming=False, learning_rate=1e-4, optimizer=optimizer) is None
    assert optimizer.param_groups[0]["lr"] == 3e-4


def test_an_optimizer_that_sets_its_own_rate_is_left_alone() -> None:
    # Upstream clears the learning rate for relative-step Adafactor.
    optimizer = Optimizer([1e-4])
    override = LearningRateOverride()
    override.capture(optimizer, Scheduler(optimizer))
    optimizer.param_groups[0]["lr"] = 3e-4
    assert override.apply(resuming=True, learning_rate=None, optimizer=optimizer) is None
    assert optimizer.param_groups[0]["lr"] == 3e-4


def test_a_scheduler_without_base_rates_uses_the_param_groups() -> None:
    # Schedule-free optimizers get upstream's dummy scheduler, which has no base_lrs.
    optimizer = Optimizer([2e-4])
    dummy = SimpleNamespace(optimizer=optimizer)
    override = LearningRateOverride()
    override.capture(optimizer, dummy)
    optimizer.load_state_dict({"param_groups": [{"lr": 1e-4}]})
    line = override.apply(resuming=True, learning_rate=2e-4, optimizer=optimizer)
    assert optimizer.param_groups[0] == {"params": [0], "lr": 2e-4, "initial_lr": 2e-4}
    assert line == "Resume learning rate: applied 0.0002; the saved state had 0.0001."


def test_a_mismatched_group_count_is_refused() -> None:
    optimizer = Optimizer([1e-4])
    override = LearningRateOverride()
    override.capture(optimizer, Scheduler(optimizer))
    optimizer.param_groups.append({"params": [1], "lr": 1e-4})
    with pytest.raises(ValueError, match="2 optimizer parameter groups"):
        override.apply(resuming=True, learning_rate=1e-4, optimizer=optimizer)


class Trainer:
    """The pinned `trainer_base` order: build the scheduler, prepare, load `--resume`, `on_train_start`, train."""

    saved: tuple[dict[str, Any], dict[str, Any]] | None = None

    def __init__(self) -> None:
        self.started = False

    def on_before_sample_images(self, *args: Any) -> None:
        pass

    def sample_images(self, *args: Any) -> None:
        pass

    def on_after_sample_images(self, *args: Any) -> None:
        pass

    def on_post_optimizer_step(self, *args: Any) -> None:
        pass

    def get_lr_scheduler(self, args: Any, optimizer: Optimizer, num_processes: int) -> Scheduler:
        return Scheduler(optimizer)

    def on_train_start(self, args: Any, accelerator: Any, network: Any, transformer: Any, optimizer: Any) -> None:
        self.started = True

    def train(self, args: Any) -> Optimizer:
        optimizer = Optimizer([args.learning_rate])
        scheduler = self.get_lr_scheduler(args, optimizer, 1)
        if args.resume and self.saved:
            optimizer.load_state_dict(self.saved[0])
            scheduler.load_state_dict(self.saved[1])
        self.on_train_start(args, None, None, None, optimizer)
        return optimizer


@pytest.mark.parametrize("resume", [True, False])
def test_the_trainer_entry_point_applies_the_rate_after_the_state_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], resume: bool
) -> None:
    class Observed(Trainer):
        saved = saved_state([1e-4])

    observe_trainer(Observed, TrainingTiming(tmp_path / "timing.json"))
    trainer = Observed()
    optimizer = trainer.train(SimpleNamespace(learning_rate=3e-4, resume="state" if resume else None))
    assert trainer.started
    assert optimizer.param_groups[0]["lr"] == 3e-4
    output = capsys.readouterr().out
    if resume:
        assert "Resume learning rate: applied 0.0003; the saved state had 0.0001." in output
    else:
        assert "Resume learning rate" not in output

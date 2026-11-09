import pytest

from musubi_gui.wan_pair import alternating_phases, expert_steps


def test_low_first_schedule_and_per_expert_counts() -> None:
    phases = alternating_phases(120, 30)
    assert [(p.expert, p.steps) for p in phases] == [("low", 30), ("high", 30), ("low", 30), ("high", 30)]
    assert [(p.low_before, p.high_before) for p in phases] == [(0, 0), (30, 0), (30, 30), (60, 30)]
    assert expert_steps(120, 30) == {"low": 60, "high": 60}


def test_partial_phase_and_restoration_keep_schedule_position() -> None:
    assert [(p.expert, p.start, p.steps) for p in alternating_phases(80, 30)] == [
        ("low", 0, 30),
        ("high", 30, 30),
        ("low", 60, 20),
    ]
    assert [(p.expert, p.start, p.steps) for p in alternating_phases(50, 30, start=40)] == [
        ("high", 40, 20),
        ("low", 60, 30),
    ]
    assert expert_steps(40, 30) == {"low": 30, "high": 10}


def test_switch_each_optimizer_step_and_short_budget() -> None:
    assert [p.expert for p in alternating_phases(4, 1)] == ["low", "high", "low", "high"]
    assert [(p.expert, p.steps) for p in alternating_phases(2, 30)] == [("low", 2)]


@pytest.mark.parametrize("steps,interval,start", [(0, 30, 0), (30, 0, 0), (30, -1, 0), (30, 1, -1)])
def test_invalid_schedule_is_rejected(steps: int, interval: int, start: int) -> None:
    with pytest.raises(ValueError):
        alternating_phases(steps, interval, start=start)


def test_excessive_phase_count_is_rejected_before_building_commands() -> None:
    with pytest.raises(ValueError, match="within 10000 phases"):
        alternating_phases(1_000_000, 1)

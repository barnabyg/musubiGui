from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.run_progress import estimated_remaining, parse_progress
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.runs_api import runs_router
from musubi_gui.settings import Settings


def test_upstream_bar_reports_step_times_and_loss() -> None:
    log = (
        "INFO steps: total optimization steps: 600\n"
        "steps:   0%|          | 0/600 [00:00<?, ?it/s]\r"
        "steps:  10%|█         | 60/600 [1:19:00<11:51:00, 79.00s/it, avr_loss=0.0812]\r"
        "steps:  10%|█         | 61/600 [1:20:19<11:49:41, 79.00s/it, avr_loss=7.5e-02]"
    )
    assert parse_progress(log) == {
        "step": 61,
        "total": 600,
        "elapsed": "1:20:19",
        "eta": "11:49:41",
        "loss": "7.5e-02",
        "seconds_per_step": 79.0,
    }


def test_bar_before_the_first_step_leaves_the_eta_unknown() -> None:
    assert parse_progress("steps:   0%|          | 0/600 [00:00<?, ?it/s]") == {
        "step": 0,
        "total": 600,
        "elapsed": None,
        "eta": None,
        "loss": None,
        "seconds_per_step": None,
    }


def test_fixture_lines_report_steps_and_loss_without_times() -> None:
    log = "steps: 1/2 loss=0.0 (synthetic optimizer_step=1)\nsteps: 2/2 loss=0.0 (synthetic optimizer_step=2)\n"
    assert parse_progress(log) == {
        "step": 2,
        "total": 2,
        "elapsed": None,
        "eta": None,
        "loss": "0.0",
        "seconds_per_step": None,
    }


@pytest.mark.parametrize(
    ("rate", "expected"), [("2.5it/s", 0.4), ("83.20s/it", 83.2), ("?it/s", None), ("0it/s", None), ("1..2s/it", None)]
)
def test_speed_uses_only_the_latest_training_bar(rate: str, expected: float | None) -> None:
    progress = parse_progress(f"steps: 1/2 [00:02<00:02, 2s/it]\rsteps: 2/2 [00:04<00:00, {rate}]\nsampling: 99s/it")
    assert progress is not None
    assert progress["seconds_per_step"] == expected


def test_missing_or_nonsense_steps_stay_unknown() -> None:
    assert parse_progress("") is None
    assert parse_progress("Loading the model\n--max_train_steps 600\nprepare steps: done") is None
    assert parse_progress("not at line start steps: 1/2") is None
    assert parse_progress("steps: 3/2") is None
    assert parse_progress("steps: 0/0") is None


def test_logs_route_reports_progress_from_the_training_log_only(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite3", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "name": "Job", "family": "wan", "revision": 1},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            stages=[],
            fingerprint="fingerprint",
        )
    )
    app = FastAPI()
    app.include_router(runs_router(service))
    client = TestClient(app)
    assert client.get(f"/api/runs/{run['id']}/logs").json()["progress"] is None
    (tmp_path / "run" / "latent_cache.log").write_text("steps: 9/9\n", encoding="utf-8")
    assert client.get(f"/api/runs/{run['id']}/logs").json()["progress"] is None
    (tmp_path / "run" / "train.log").write_text("x" * 100_000 + "\nsteps: 1/2 loss=0.5\n", encoding="utf-8")
    assert client.get(f"/api/runs/{run['id']}/logs").json()["progress"]["step"] == 1


def test_paired_progress_keeps_reported_eta_with_phase_scope(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite3", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "name": "Job", "family": "wan", "revision": 1, "config": {"steps": 8}},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            stages=[],
            fingerprint="fingerprint",
        )
    )
    service.store.update(
        run["id"],
        lambda r: r.update(
            stages=[
                {
                    "name": "train",
                    "status": "running",
                    "log": "train.log",
                    "training": {"expert": "high", "start": 4, "interval": 4},
                }
            ]
        ),
    )
    (tmp_path / "run" / "train-high-00000004.log").write_text(
        "steps: 50%| | 2/4 [00:20<00:20, 10s/it, loss=0.5]\n", encoding="utf-8"
    )
    progress = service.progress(run["id"])
    assert progress is not None
    assert progress["step"] == 6
    assert progress["eta"] == "00:20"
    assert progress["eta_scope"] == "phase"
    assert progress["estimated_remaining_seconds"] == 20


@pytest.mark.parametrize(
    ("step", "total", "phase_step", "phase_total", "elapsed", "eta", "expected"),
    [
        (2, 10, 2, 10, "00:20", "01:20", 80),
        (6, 20, 2, 4, "00:20", "00:20", 140),
        (4, 12, 4, 4, "00:40", "00:00", 80),
        (2, 10, 2, 10, "1:00:00", None, 14400),
        (0, 10, 0, 10, "00:00", None, None),
        (2, 10, 2, 10, None, None, None),
        (2, 10, 2, 10, "00:00", "00:00", None),
        (10, 10, 10, 10, None, None, 0),
    ],
)
def test_estimated_remaining_extrapolates_observed_phase_speed(
    step: int, total: int, phase_step: int, phase_total: int, elapsed: str | None, eta: str | None, expected: int | None
) -> None:
    assert (
        estimated_remaining(
            {
                "step": step,
                "total": total,
                "phase_step": phase_step,
                "phase_total": phase_total,
                "elapsed": elapsed,
                "eta": eta,
            }
        )
        == expected
    )

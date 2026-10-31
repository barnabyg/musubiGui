from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.run_progress import parse_progress
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
    assert parse_progress(log) == {"step": 61, "total": 600, "elapsed": "1:20:19", "eta": "11:49:41", "loss": "7.5e-02"}


def test_bar_before_the_first_step_leaves_the_eta_unknown() -> None:
    assert parse_progress("steps:   0%|          | 0/600 [00:00<?, ?it/s]") == {
        "step": 0,
        "total": 600,
        "elapsed": None,
        "eta": None,
        "loss": None,
    }


def test_fixture_lines_report_steps_and_loss_without_times() -> None:
    log = "steps: 1/2 loss=0.0 (synthetic optimizer_step=1)\nsteps: 2/2 loss=0.0 (synthetic optimizer_step=2)\n"
    assert parse_progress(log) == {"step": 2, "total": 2, "elapsed": None, "eta": None, "loss": "0.0"}


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

"""Completion forecasts observed through the persisted Run API."""

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui.recipe import Stage
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.runs_api import runs_router
from musubi_gui.settings import Settings


@pytest.mark.parametrize(
    ("completed", "preview_seconds", "active", "step", "outstanding", "budget", "remaining"),
    [
        (1, 600, None, 2, 9, 5400, 5580),
        (1, 600, {"step": 4, "started": "2026-11-15T10:00:00Z"}, 4, 9, 5280, 5440),
        (2, 1500, None, 4, 8, 6000, 6160),
        (0, 0, None, 2, 10, None, None),
    ],
)
def test_native_preview_forecast_uses_measured_wall_time_without_counting_it_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed: int,
    preview_seconds: int,
    active: dict | None,
    step: int,
    outstanding: int,
    budget: int | None,
    remaining: int | None,
) -> None:
    monkeypatch.setattr("musubi_gui.runs_api.utc_now", lambda: "2026-11-15T10:02:00Z")
    root = tmp_path / "state"
    service = RunService(root, device_path=tmp_path / "device.sqlite", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "name": "Job", "family": "h3", "revision": 1, "config": {"steps": 20}},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            stages=[Stage("train", ["python", "trainer.py", "--max_train_steps", "20", "--sample_every_n_steps", "2"])],
            fingerprint="timing",
        )
    )
    service.store.update(
        run["id"],
        lambda r: r.update(
            status="training",
            stages=[
                {
                    **r["stages"][0],
                    "status": "running",
                    "started": "2026-11-15T10:00:00Z",
                }
            ],
        ),
    )
    (tmp_path / "run/train.log").write_text("steps: 2/20 [10:20<1:33:00, 310s/it]\n", encoding="utf-8")
    (tmp_path / "run/timing-train.json").write_text(
        json.dumps(
            {
                "version": 1,
                "training": {"step": step, "intervals": 1, "seconds": 10},
                "previews": {"completed": completed, "seconds": preview_seconds, "active": active},
            }
        ),
        encoding="utf-8",
    )

    def forecast(current: RunService) -> dict:
        app = FastAPI()
        app.include_router(runs_router(current))
        return TestClient(app).get(f"/api/runs/{run['id']}/logs").json()["run_estimate"]

    estimate = forecast(service)
    assert estimate["outstanding_previews"] == outstanding
    assert estimate["preview_remaining_seconds"] == budget
    assert estimate["training_remaining_seconds"] == (180 if step == 2 else 160)
    assert estimate["remaining_seconds"] == remaining
    if remaining is not None:
        assert estimate["finish_at"] == (
            "2026-11-15T11:35:00+00:00"
            if remaining == 5580
            else "2026-11-15T11:32:40+00:00"
            if remaining == 5440
            else "2026-11-15T11:44:40+00:00"
        )
    else:
        assert estimate["finish_at"] is None
    if completed == 1 and step == 2:
        service.store.update(
            run["id"], lambda r: r["stages"].append({"name": "generate", "args": [], "status": "pending"})
        )
        partial = forecast(service)
        assert partial["remaining_seconds"] is None
        assert partial["finish_at"] is None
        assert partial["preview_remaining_seconds"] == 5400
        assert partial["accounted_remaining_seconds"] == 5580
        assert partial["accounted_finish_at"] == "2026-11-15T11:35:00+00:00"
        assert "final sampling" in partial["unknown_components"]
        service.store.update(run["id"], lambda r: r["stages"].pop())
    restarted = RunService(root, device_path=tmp_path / "device.sqlite", autostart=False)
    assert forecast(restarted)["remaining_seconds"] == remaining


@pytest.mark.parametrize("status", ["generating", "completed", "failed", "stopped", "cancelled", "interrupted"])
def test_paired_segment_counts_its_frozen_previews_and_separate_final_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    monkeypatch.setattr("musubi_gui.runs_api.utc_now", lambda: "2026-11-15T10:02:00Z")
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={
                "id": "pair",
                "name": "Pair",
                "family": "wan",
                "revision": 1,
                "config": {"steps": 1000, "preview_every": 2, "sample_enabled": True},
            },
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            stages=[Stage("generate", [], preview_step=step) for step in [4, 6, 8]] + [Stage("generate", [])],
            fingerprint="segment",
        )
    )

    def update(record: dict) -> None:
        record.update(status=status, segment={"schedule_start": 6, "additional_steps": 2})
        record["stages"][0].update(status="completed", started="2026-11-15T09:50:00Z", finished="2026-11-15T10:00:00Z")
        record["stages"][1].update(status="running", started="2026-11-15T10:00:00Z")

    service.store.update(run["id"], update)
    app = FastAPI()
    app.include_router(runs_router(service))
    estimate = TestClient(app).get(f"/api/runs/{run['id']}/logs").json()["run_estimate"]
    if status == "generating":
        assert estimate["outstanding_previews"] == 2
        assert estimate["preview_remaining_seconds"] == 1080
        assert estimate["outstanding_final_samples"] == 1
        assert estimate["sampling_remaining_seconds"] == 600
        assert estimate["remaining_seconds"] == 1680
    else:
        assert estimate["remaining_seconds"] == (0 if status == "completed" else None)
        assert estimate["finish_at"] is None


def test_native_trainer_hooks_remove_preview_wall_time_from_observed_step_intervals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The synthetic upstream is the external trainer boundary. Exercise the real
    # app-owned entry point and storage/API, including a post-preview update.
    package = tmp_path / "musubi_tuner"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "wan_train_network.py").write_text(
        "import time\nfrom pathlib import Path\nfrom types import SimpleNamespace\n"
        "class WanNetworkTrainer:\n"
        " def on_before_sample_images(self,*args): pass\n"
        " def on_after_sample_images(self,*args): pass\n"
        " def on_post_optimizer_step(self,*args): pass\n"
        " def sample_images(self,*args):\n"
        "  path=Path('checkpoints/sample/fixture_000002_00.mp4')\n"
        "  path.parent.mkdir(parents=True,exist_ok=True)\n"
        "  path.write_bytes(b'synthetic')\n"
        "def main():\n"
        " trainer=WanNetworkTrainer()\n original=time.monotonic\n"
        " times=iter([0,10,10,610,620])\n time.monotonic=lambda:next(times)\n"
        " trainer.on_post_optimizer_step(None,None,None,None,True,0)\n"
        " trainer.on_post_optimizer_step(None,None,None,None,True,1)\n"
        " args=(None,SimpleNamespace(output_dir='checkpoints'),None,2)\n"
        " trainer.on_before_sample_images(*args)\n trainer.sample_images(*args)\n"
        " trainer.on_after_sample_images(*args)\n"
        " trainer.on_post_optimizer_step(None,None,None,None,True,2)\n"
        " time.monotonic=original\n print('steps: 3/20 loss=0.5',flush=True)\n"
        " Path('ready').touch()\n deadline=time.monotonic()+900\n"
        " while not Path('release').exists() and time.monotonic()<deadline: time.sleep(.02)\n",
        encoding="utf-8",
    )
    script = tmp_path / "wan_train_network.py"
    script.write_text("from musubi_tuner.wan_train_network import main\nmain()\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite")
    run = service.submit(
        ExecutionPlan(
            job={"id": "hook", "name": "Hook", "family": "wan", "revision": 1},
            settings=Settings(checkout=str(tmp_path), python=sys.executable),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            fingerprint="hooks",
            stages=[
                Stage("train", [sys.executable, str(script), "--max_train_steps", "20", "--sample_every_n_steps", "2"])
            ],
        ),
        start_after_prepare=True,
    )
    try:
        deadline = time.monotonic() + 180
        while not (tmp_path / "ready").exists():
            assert service.get(run["id"])["status"] != "failed", service.get(run["id"])
            assert time.monotonic() < deadline
            time.sleep(0.02)
        app = FastAPI()
        app.include_router(runs_router(service))
        estimate = TestClient(app).get(f"/api/runs/{run['id']}/logs").json()["run_estimate"]
        assert estimate["training_remaining_seconds"] == 170
        assert estimate["preview_remaining_seconds"] == 5400
        assert estimate["remaining_seconds"] == 5570
    finally:
        (tmp_path / "release").touch()
        deadline = time.monotonic() + 180
        while service.get(run["id"])["status"] in {"training", "pending_training", "preparing", "pending_preparation"}:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        service.close()


def test_single_update_wan_phases_budget_loading_and_final_save_once(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "short", "name": "Short phases", "family": "wan", "revision": 1},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            fingerprint="short",
            stages=[
                Stage("train", ["python", "trainer.py", "--max_train_steps", "1"], {"expert": "low", "start": 0}),
                Stage("generate", [], preview_step=1),
                Stage("train", ["python", "trainer.py", "--max_train_steps", "1"], {"expert": "low", "start": 2}),
                Stage("generate", [], preview_step=2),
            ],
        )
    )

    def update(record: dict) -> None:
        record.update(status="training")
        record["stages"][0].update(status="completed", started="2026-11-15T09:00:00Z", finished="2026-11-15T09:00:11Z")
        record["stages"][1].update(status="completed", started="2026-11-15T09:00:11Z", finished="2026-11-15T09:10:11Z")

    service.store.update(run["id"], update)
    (tmp_path / "run/timing-train-low-00000000.json").write_text(
        json.dumps(
            {
                "version": 1,
                "training": {
                    "step": 1,
                    "intervals": 0,
                    "seconds": 0,
                    "first_step_at": "2026-11-15T09:00:10Z",
                    "last_step_at": "2026-11-15T09:00:10Z",
                },
                "previews": {"completed": 0, "seconds": 0, "active": None},
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(runs_router(service))
    estimate = TestClient(app).get(f"/api/runs/{run['id']}/logs").json()["run_estimate"]
    assert estimate["training_remaining_seconds"] == 10
    assert estimate["overhead_remaining_seconds"] == 1
    assert estimate["preview_remaining_seconds"] == 600
    assert estimate["remaining_seconds"] == 611


@pytest.mark.parametrize(("status", "expected_overhead"), [("pending", 601), ("running", 481)])
def test_measured_loading_budget_remains_while_a_later_phase_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_overhead: int,
) -> None:
    monkeypatch.setattr("musubi_gui.runs_api.utc_now", lambda: "2026-11-15T10:02:00Z")
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "loading", "name": "Loading", "family": "wan", "revision": 1},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            fingerprint="loading",
            stages=[
                Stage("train", ["python", "trainer.py", "--max_train_steps", "100"], {"expert": "low", "start": start})
                for start in [0, 200]
            ],
        )
    )

    def update(record: dict) -> None:
        record.update(status="training")
        record["stages"][0].update(status="completed", started="2026-11-15T09:00:00Z", finished="2026-11-15T09:26:41Z")
        record["stages"][1].update(status=status, started="2026-11-15T10:00:00Z" if status == "running" else None)

    service.store.update(run["id"], update)
    (tmp_path / "run/timing-train-low-00000000.json").write_text(
        json.dumps(
            {
                "version": 1,
                "training": {
                    "step": 100,
                    "intervals": 99,
                    "seconds": 990,
                    "first_step_at": "2026-11-15T09:10:10Z",
                    "last_step_at": "2026-11-15T09:26:40Z",
                },
                "previews": {"completed": 0, "seconds": 0, "active": None},
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(runs_router(service))
    estimate = TestClient(app).get(f"/api/runs/{run['id']}/logs").json()["run_estimate"]
    assert estimate["training_remaining_seconds"] == 1000
    assert estimate["overhead_remaining_seconds"] == expected_overhead
    assert estimate["remaining_seconds"] == 1000 + expected_overhead


def test_restored_segment_forecast_reads_global_timing_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("musubi_gui.runs_api.utc_now", lambda: "2026-11-15T10:02:00Z")
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "name": "Job", "family": "h3", "revision": 1, "config": {"steps": 20}},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "run",
            cache=tmp_path / "cache",
            stages=[Stage("train", ["python", "trainer.py", "--max_train_steps", "5", "--sample_every_n_steps", "2"])],
            fingerprint="timing",
        )
    )
    # Resumed at 3 to train until 8: previews fall due at global 4, 6 and 8.
    service.store.update(
        run["id"],
        lambda r: r.update(
            status="training",
            segment={"target_steps": 8, "step_offset": 3, "additional_steps": 5},
            stages=[{**r["stages"][0], "status": "running", "started": "2026-11-15T10:00:00Z"}],
        ),
    )
    (tmp_path / "run/train.log").write_text("steps: 3/5 [00:30<00:20, 10s/it]\n", encoding="utf-8")
    (tmp_path / "run/timing-train.json").write_text(
        json.dumps(
            {
                "version": 1,
                "training": {"step": 6, "intervals": 2, "seconds": 20},
                "previews": {"completed": 2, "seconds": 100, "active": None},
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(runs_router(service))
    response = TestClient(app).get(f"/api/runs/{run['id']}/logs").json()
    estimate = response["run_estimate"]
    assert (estimate["completed_previews"], estimate["outstanding_previews"]) == (2, 1)
    assert estimate["training_remaining_seconds"] == 20
    assert estimate["preview_remaining_seconds"] == 50
    assert (response["progress"]["step"], response["progress"]["total"]) == (6, 8)

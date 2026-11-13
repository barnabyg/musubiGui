import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from musubi_gui import run_performance
from musubi_gui.run_performance import RunPerformance, gpu_samples
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.runs_api import runs_router
from musubi_gui.settings import Settings


def monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunPerformance:
    # Drive samples deterministically without racing the production collector thread.
    monkeypatch.setattr(RunPerformance, "_collect", lambda self: None)
    log = tmp_path / "train.log"
    log.write_text("steps: 0/10\r", encoding="utf-8")
    return RunPerformance(
        {"id": "run", "directory": str(tmp_path), "job": {"config": {"rank": 32}}},
        {"name": "train", "args": ["trainer.py"]},
        os.getpid(),
        log,
        fixture=True,
    )


def test_step_intervals_ignore_repeated_bar_updates_and_capture_long_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = monitor(tmp_path, monkeypatch)
    try:
        assert collector._progress(0)["observed_seconds_per_step"] is None
        collector.log.write_text("steps: 1/10 [01:30<13:30, 90s/it]\r", encoding="utf-8")
        assert collector._progress(90)["observed_seconds_per_step"] == 90
        assert collector._progress(95)["observed_seconds_per_step"] is None
        collector.log.write_text("steps: 2/10 [04:30<36:00, 135s/it]\r", encoding="utf-8")
        progress = collector._progress(270)
        assert progress["observed_seconds_per_step"] == 180
        assert progress["seconds_per_step"] == 135
    finally:
        collector.close(0)
    assert not collector.thread.is_alive()


def test_failed_gpu_query_still_records_memory_and_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collector = monitor(tmp_path, monkeypatch)

    def timeout(executable: str | None) -> dict:
        raise subprocess.TimeoutExpired("nvidia-smi", 10)

    monkeypatch.setattr(run_performance, "gpu_samples", timeout)
    try:
        sample = collector.sample()
        assert sample["host_available_bytes"] > 0
        assert sample["process_tree_rss_bytes"] > 0
        assert sample["progress"]["step"] == 0
        assert sample["errors"] == ["gpu: TimeoutExpired"]
    finally:
        collector.close()


def test_gpu_query_keeps_device_identity_and_unsupported_fields_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def query(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(
            args, 0, "0, RTX 3090, GPU-example, 22000, 24576, 98, 70, 245, 280, 72, 1500, [N/A], P2\n"
        )

    monkeypatch.setattr(run_performance.subprocess, "run", query)
    result = gpu_samples("nvidia-smi")
    assert result["gpus"][0]["memory_clock_mhz"] is None
    assert result["gpus"][0]["power_watts"] == 245
    assert result["gpus"][0]["name"] == "RTX 3090"
    assert gpu_samples(None)["gpus"] is None


def test_report_redacts_credentials_and_download_snapshots_complete_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXAMPLE_API_KEY", "secret-example-credential")
    collector = monitor(tmp_path, monkeypatch)
    collector._write({"event": "probe", "value": "secret-example-credential"})
    collector.close(0)
    assert "secret-example-credential" not in collector.path.read_text(encoding="utf-8")
    with collector.path.open("a", encoding="utf-8") as stream:
        stream.write('{"partial":')
    service = RunService(tmp_path / "state", autostart=False)
    run = service.submit(
        ExecutionPlan(
            job={"id": "h3", "name": "H3", "family": "h3", "revision": 1},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path,
            cache=tmp_path / "cache",
            stages=[],
            fingerprint="h3",
        )
    )
    app = FastAPI()
    app.include_router(runs_router(service))
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run['id']}/performance")
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("attachment;")
        assert all(json.loads(line) for line in response.text.splitlines())
        assert client.get(f"/api/runs/{run['id']}/logs").json()["performance_available"]
        collector.path.unlink()
        assert client.get(f"/api/runs/{run['id']}/performance").status_code == 404
        assert client.get("/api/runs/missing/performance").status_code == 409


def test_unwritable_report_does_not_raise_or_leave_a_collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "performance.jsonl").mkdir()
    collector = monitor(tmp_path, monkeypatch)
    collector.close()
    assert not collector.enabled
    assert not collector.thread.is_alive()
    assert "Performance diagnostics unavailable" in collector.log.read_text(encoding="utf-8")

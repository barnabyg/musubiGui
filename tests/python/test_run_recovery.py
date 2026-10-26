import json
import sqlite3
import sys
import time
from pathlib import Path

from musubi_gui.processes import identity_status, process_identity
from musubi_gui.reference import Stage
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings


def wait_status(service: RunService, run_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == expected:
            return run
        if run["status"] in {"prepared", "completed", "failed"}:
            raise AssertionError(run)
        time.sleep(0.05)
    raise AssertionError(service.get(run_id))


def test_surviving_descendant_retains_gpu_ownership_after_its_launcher_exits(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    script = work / "launcher.py"
    script.write_text(
        "import subprocess,sys,time,os\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])\ntime.sleep(.3)\nos._exit(0)\n",
        encoding="utf-8",
    )
    service = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite")
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "revision": 1, "family": "wan"},
            settings=Settings(checkout=str(work), python=sys.executable),
            manifest={"items": []},
            directory=work,
            cache=work,
            stages=[Stage("latent_cache", [sys.executable, str(script)])],
            fingerprint="orphan",
        )
    )
    try:
        orphaned = wait_status(service, run["id"], "orphaned")
        assert orphaned["processes"]
        assert service.device.owner()["run_id"] == run["id"]
        service.reconcile(run["id"])
        assert service.get(run["id"])["status"] == "orphaned"
        service.terminate_orphan(run["id"], confirmed=True)
        interrupted = wait_status(service, run["id"], "interrupted")
        assert interrupted["processes"] == []
        assert service.device.owner() is None
    finally:
        # Clean up only the exact process identities observed by this isolated worker.
        from musubi_gui.processes import terminate_owned

        terminate_owned(service.get(run["id"]).get("processes", []), force=True)
        service.close()


def test_restart_observes_surviving_work_and_does_not_launch_replacement(tmp_path: Path) -> None:
    root, device = tmp_path / "state", tmp_path / "device.sqlite"
    work = tmp_path / "work"
    work.mkdir()
    service = RunService(root, device_path=device)
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "revision": 1, "family": "wan"},
            settings=Settings(checkout=str(work), python=sys.executable),
            manifest={"items": []},
            directory=work,
            cache=work,
            stages=[
                Stage("latent_cache", [sys.executable, "-c", "import time;print('alive',flush=True);time.sleep(20)"])
            ],
            fingerprint="restart",
        )
    )
    deadline = time.monotonic() + 5
    while not service.get(run["id"]).get("processes") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.get(run["id"])["processes"]
    service.close()
    restarted = RunService(root, device_path=device)
    try:
        assert restarted.get(run["id"])["status"] == "orphaned"
        assert restarted.device.owner()["run_id"] == run["id"]
        assert "alive" in restarted.logs(run["id"])
        restarted.terminate_orphan(run["id"], confirmed=True)
        assert restarted.get(run["id"])["status"] == "interrupted"
        assert restarted.device.owner() is None
    finally:
        from musubi_gui.processes import terminate_owned

        terminate_owned(restarted.get(run["id"]).get("processes", []), force=True)
        restarted.close()


def test_stale_pid_and_device_lock_are_reconciled_without_touching_reused_process(tmp_path: Path) -> None:
    root, device = tmp_path / "state", tmp_path / "device.sqlite"
    service = RunService(root, device_path=device, autostart=False)
    current = process_identity()
    stale = {**current, "created": current["created"] - 1000}
    run = service.submit(
        ExecutionPlan(
            job={"id": "job", "revision": 1, "family": "wan"},
            settings=Settings(),
            manifest={"items": []},
            directory=tmp_path / "output",
            cache=tmp_path / "cache",
            stages=[],
            fingerprint="stale",
        )
    )
    # A durable interruption fixture models a dead worker and a reused PID.
    record = {**run, "status": "training", "controller": stale, "processes": [stale]}
    with sqlite3.connect(root / "runs.sqlite3") as database:
        database.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(record), run["id"]))
    assert service.device.acquire(run["id"], stale)
    service.device.update(run["id"], [stale])
    service.close()
    restarted = RunService(root, device_path=device)
    try:
        assert restarted.get(run["id"])["status"] == "interrupted"
        assert restarted.device.owner() is None
        assert identity_status(current) == "alive"
    finally:
        restarted.close()

import shutil
import subprocess
import sys
import time
from pathlib import Path

from musubi_gui.reference import Stage
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings
from musubi_gui.snapshots import checkout_identity


def plan(root: Path, name: str = "one", delay: float = 0.05) -> ExecutionPlan:
    work = root / name
    work.mkdir()
    script = work / "stage.py"
    script.write_text(
        "import pathlib,time\n"
        f"time.sleep({delay})\n"
        "print('cache complete', flush=True)\n"
        "pathlib.Path('cache.safetensors').write_bytes(b'cache')\n",
        encoding="utf-8",
    )
    settings = Settings(
        checkout=str(work), python=sys.executable, output_root=str(root / "output"), cache_root=str(root / "cache")
    )
    return ExecutionPlan(
        job={"id": name, "revision": 1, "family": "wan", "name": name},
        settings=settings,
        manifest={"items": [{"path": "cube.mp4", "sha256": "example", "caption": {"text": "red square"}}]},
        directory=work,
        cache=work,
        stages=[Stage("latent_cache", [sys.executable, str(script)])],
        fingerprint=name,
    )


def wait_for(service: RunService, run_id: str, status: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == status:
            return run
        time.sleep(0.02)
    raise AssertionError(service.get(run_id))


def test_preparation_persists_logs_and_deduplicates_requests(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        execution = plan(tmp_path)
        run = service.submit(execution)
        assert service.submit(execution)["id"] == run["id"]
        prepared = wait_for(service, run["id"], "prepared")
        assert prepared["stages"][0]["exit_code"] == 0
        assert "cache complete" in service.logs(run["id"])
    finally:
        service.close()
    restarted = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        assert restarted.get(run["id"])["status"] == "prepared"
        assert restarted.get(run["id"])["manifest"]["items"][0]["caption"]["text"] == "red square"
    finally:
        restarted.close()


def test_failed_preparation_is_visible_and_retry_preserves_the_original_inputs(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        execution = plan(tmp_path)
        execution.stages.insert(0, Stage("text_cache", [sys.executable, "-c", "raise SystemExit(3)"]))
        run = service.submit(execution)
        failed = wait_for(service, run["id"], "failed")
        assert "exited 3" in failed["error"]
        retried = service.retry(run["id"])
        assert retried["manifest"] == run["manifest"]
        assert retried["id"] != run["id"]
        assert retried["retry_of"] == run["id"]
        wait_for(service, retried["id"], "failed")
    finally:
        service.close()


def test_start_runs_training_once_and_keeps_the_prepared_job_revision(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        execution = plan(tmp_path)
        train = execution.directory / "train.py"
        train.write_text(
            "import pathlib,time\ntime.sleep(.2)\nprint('step 1/2',flush=True)\npathlib.Path('checkpoints').mkdir(exist_ok=True)\npathlib.Path('checkpoints/test.safetensors').write_bytes(b'checkpoint')\n",
            encoding="utf-8",
        )
        execution.stages.append(Stage("train", [sys.executable, str(train)]))
        run = service.submit(execution)
        wait_for(service, run["id"], "prepared")
        started = service.start(run["id"])
        assert service.start(run["id"])["id"] == started["id"]
        completed = wait_for(service, run["id"], "completed")
        assert completed["job"]["revision"] == 1
        assert completed["exit_code"] == 0
        assert "step 1/2" in service.logs(run["id"])
    finally:
        service.close()


def test_h3_preparation_rejects_missing_unconditional_guidance_cache(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        execution = plan(tmp_path)
        execution.job["family"] = "h3"
        execution.stages = [Stage("text_cache", [sys.executable, "-c", "print('partial cache')"])]
        run = service.submit(execution)
        failed = wait_for(service, run["id"], "failed")
        assert "unconditional guidance cache" in failed["error"]
    finally:
        service.close()


def test_queue_order_and_cancellation_survive_restart(tmp_path: Path) -> None:
    state, device = tmp_path / "state", tmp_path / "gpu.sqlite"
    service = RunService(state, device_path=device)
    ids = []
    try:
        for name in ["first", "second"]:
            execution = plan(tmp_path, name)
            script = execution.directory / "train.py"
            script.write_text(
                "import pathlib\npathlib.Path('checkpoints').mkdir()\npathlib.Path('checkpoints/final.safetensors').write_bytes(b'checkpoint')\n",
                encoding="utf-8",
            )
            execution.stages.append(Stage("train", [sys.executable, str(script)]))
            run = service.submit(execution)
            wait_for(service, run["id"], "prepared")
            ids.append(run["id"])
    finally:
        service.close()
    paused = RunService(state, device_path=device, autostart=False)
    for run_id in ids:
        paused.enqueue(run_id)
    paused.reorder(list(reversed(ids)))
    assert [run["id"] for run in paused.queue()] == list(reversed(ids))
    paused.cancel(ids[1])
    paused.close()
    restarted = RunService(state, device_path=device)
    try:
        wait_for(restarted, ids[0], "completed")
        assert restarted.get(ids[1])["status"] == "cancelled"
        assert not (tmp_path / "second/checkpoints").exists()
        assert restarted.device.owner() is None
    finally:
        restarted.close()


def test_changed_training_source_blocks_execution_of_prepared_inputs(tmp_path: Path) -> None:
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite", autostart=False)
    execution = plan(tmp_path)
    git = shutil.which("git")
    assert git
    for arguments in [
        ["init", "--quiet"],
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run([git, "-C", str(execution.directory), *arguments], check=True, capture_output=True)
    execution.provenance["checkout"] = checkout_identity(execution.directory)
    run = service.submit(execution)
    (execution.directory / "stage.py").write_text("print('changed source')", encoding="utf-8")
    service.start_worker()
    try:
        failed = wait_for(service, run["id"], "failed")
        assert "checkout changed" in failed["error"]
        assert not (execution.directory / "latent_cache.log").exists()
    finally:
        service.close()

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from musubi_gui.processes import observed_tree, terminate_owned
from musubi_gui.runs import RunService
from musubi_gui.windows_job import ProcessJob


@pytest.mark.parametrize("fast_launcher", [False, True])
def test_controller_crash_before_pid_persistence_leaves_child_discoverable_by_job(
    tmp_path: Path, fast_launcher: bool
) -> None:
    controller = tmp_path / "controller.py"
    controller.write_text(
        "import json,os,subprocess,sys\nfrom pathlib import Path\n"
        "from musubi_gui.processes import process_identity\n"
        "from musubi_gui.runs import ExecutionPlan,RunService\n"
        "from musubi_gui.settings import Settings\n"
        "from musubi_gui.windows_job import ProcessJob\n"
        "root=Path(sys.argv[1])\nwork=root/'work'\nwork.mkdir()\n"
        "service=RunService(root/'state',device_path=root/'device.sqlite',autostart=False)\n"
        "run=service.submit(ExecutionPlan(job={'id':'job','revision':1,'family':'wan'},settings=Settings(),manifest={'items':[]},directory=work,cache=root/'cache',stages=[],fingerprint='creation-window'))\n"
        "name='Local'+chr(92)+'MusubiGui-atomic-test-'+run['id']\njob=ProcessJob(name)\nidentity=process_identity()\n"
        "service.store.update(run['id'],lambda r:r.update(status='preparing',controller=identity,job_objects=[name],launch_pending=True,launch_protocol=3))\n"
        "assert service.device.acquire(run['id'],identity)\n"
        "service.device.update(run['id'],[],job_objects=[name],launch_pending=True,launch_protocol=3)\n"
        "with (work/'latent_cache.log').open('a',encoding='utf-8') as log:\n"
        " child=job.spawn([sys.executable,'-c',sys.argv[3]],cwd=str(work),env=dict(os.environ),log=log)\n"
        " if sys.argv[2]=='fast': assert child.wait(10)==0\n"
        " cleanup=[process_identity(pid) for pid in job.pids()]\n"
        " assert cleanup\n"
        " print(json.dumps({'run_id':run['id'],'name':name,'cleanup_identities':cleanup}),flush=True)\n"
        " os._exit(0)\n",
        encoding="utf-8",
    )
    sleeping_child = 'import time;print("running",flush=True);time.sleep(30)'
    child_code = (
        f"import os,subprocess,sys;subprocess.Popen([sys.executable,'-c',{sleeping_child!r}],close_fds=True);os._exit(0)"
        if fast_launcher
        else sleeping_child
    )
    result = subprocess.run(
        [sys.executable, str(controller), str(tmp_path), "fast" if fast_launcher else "direct", child_code],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    witness = json.loads(result.stdout.strip())
    restarted = RunService(tmp_path / "state", device_path=tmp_path / "device.sqlite")
    try:
        recovered = restarted.get(witness["run_id"])
        assert recovered["status"] == "orphaned"
        assert recovered["processes"]
        assert restarted.device.owner()["run_id"] == witness["run_id"]
        assert restarted.device.owner()["processes"]
        restarted.terminate_orphan(witness["run_id"], confirmed=True)
        assert restarted.get(witness["run_id"])["status"] == "interrupted"
        assert restarted.device.owner() is None
    finally:
        restarted.close()
        # The controller's stdout is used only to clean up this exact test-owned process tree.
        # Recovery assertions above must discover ownership without a persisted PID.
        job = ProcessJob(witness["name"], create=False)
        try:
            job.terminate()
        finally:
            job.close()
        owned = list(witness["cleanup_identities"])
        for identity in witness["cleanup_identities"]:
            owned.extend(observed_tree(identity["pid"]))
        terminate_owned(owned, force=True)


def test_native_launch_preserves_arguments_environment_stdio_and_exit_259(tmp_path: Path) -> None:
    folder = tmp_path / "工作 with spaces"
    folder.mkdir()
    output = folder / "stdio.log"
    arguments = ["space value", 'embedded"quote', "trailing\\", "", "雪と音", "line\nsecond line"]
    code = (
        "import json,os,sys;"
        "print(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd(),'value':os.environ['MUSUBI_ATOMIC_VALUE'],'stdin':sys.stdin.read()}),flush=True);"
        "print('stderr-雪',file=sys.stderr,flush=True);sys.exit(259)"
    )
    job = ProcessJob("Local\\MusubiGui-arguments-" + folder.parent.name)
    child = None
    try:
        with output.open("w", encoding="utf-8") as log:
            child = job.spawn(
                [sys.executable, "-c", code, *arguments],
                cwd=str(folder),
                env={**os.environ, "PYTHONUTF8": "1", "MUSUBI_ATOMIC_VALUE": "路径 Ω=雪"},
                log=log,
            )
            assert child.wait(10) == 259
            assert child.poll() == 259
        lines = output.read_text(encoding="utf-8").splitlines()
        result = json.loads(lines[0])
        assert result == {"args": arguments, "cwd": str(folder), "value": "路径 Ω=雪", "stdin": ""}
        assert lines[1] == "stderr-雪"
    finally:
        job.terminate()
        if child:
            child.close()
        job.close()


def test_failed_native_creation_preserves_the_creators_log_and_has_no_job_members(tmp_path: Path) -> None:
    output = tmp_path / "failed-launch.log"
    job = ProcessJob("Local\\MusubiGui-creation-failure-" + tmp_path.name)
    try:
        with output.open("w", encoding="utf-8") as log:
            with pytest.raises(OSError):
                job.spawn([str(tmp_path / "missing-python.exe")], cwd=str(tmp_path), env=dict(os.environ), log=log)
            assert job.pids() == []
            log.write("The creator still owns its log.")
        assert output.read_text(encoding="utf-8") == "The creator still owns its log."
    finally:
        job.close()

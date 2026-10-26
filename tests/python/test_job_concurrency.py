import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import pytest


def request(url: str, method: str = "GET", data: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    target = urlsplit(url)
    connection = HTTPConnection(target.hostname or "127.0.0.1", target.port, timeout=15)
    try:
        connection.request(method, target.path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


@contextmanager
def backend(root: Path) -> Iterator[str]:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # Only the real public job router is hosted: no training worker or shared GPU resource is started.
    code = (
        "import sys,uvicorn\nfrom pathlib import Path\nfrom fastapi import FastAPI\n"
        "from musubi_gui.jobs import jobs_router\napp=FastAPI()\n"
        "app.include_router(jobs_router(Path(sys.argv[1])))\n"
        "uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),access_log=False,log_level='error')\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(port)],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONUTF8": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.communicate(timeout=1)[0])
            try:
                if request(url + "/api/jobs")[0] == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("Job backend did not start")
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


@pytest.mark.parametrize("second_action", ["save", "convert", "preset"])
def test_two_backends_publish_only_one_revision_and_preserve_history(tmp_path: Path, second_action: str) -> None:
    with backend(tmp_path) as first, backend(tmp_path) as second:
        status, original = request(
            first + "/api/jobs", "POST", {"name": "Original", "config": {"sample_prompt": "A moving square. " * 30000}}
        )
        assert status == 200
        job_id = original["id"]
        preset_status, preset = request(first + f"/api/jobs/{job_id}/presets", "POST", {"name": "Reusable settings"})
        assert preset_status == 200
        current = original
        for attempt in range(5):
            gate = threading.Barrier(2)
            update = {key: value for key, value in current.items() if key not in {"id", "version"}}
            update["name"] = f"First edit {attempt}"
            other = {**update, "name": f"Second edit {attempt}"}

            def change(url: str, action: str) -> tuple[int, dict[str, Any]]:
                gate.wait(timeout=10)
                if action == "preset":
                    return request(
                        url + f"/api/jobs/{job_id}/apply-preset",
                        "POST",
                        {"preset_id": preset["id"], "revision": current["revision"]},
                    )
                if action == "convert":
                    return request(
                        url + f"/api/jobs/{job_id}/convert",
                        "POST",
                        {
                            "family": "h3" if current["family"] == "wan" else "wan",
                            "revision": current["revision"],
                            "confirmed": True,
                        },
                    )
                return request(url + f"/api/jobs/{job_id}", "PUT", update if url == first else other)

            with ThreadPoolExecutor(max_workers=2) as pool:
                calls = [pool.submit(change, first, "save"), pool.submit(change, second, second_action)]
                responses = [call.result(timeout=20) for call in calls]
            assert sorted(code for code, _ in responses) == [200, 409]
            winner = next(value for code, value in responses if code == 200)
            assert winner["revision"] == current["revision"] + 1
            assert request(first + f"/api/jobs/{job_id}")[1] == winner
            assert request(second + f"/api/jobs/{job_id}/revisions/{current['revision']}")[1] == current
            assert request(second + f"/api/jobs/{job_id}/revisions/1")[1] == original
            current = winner

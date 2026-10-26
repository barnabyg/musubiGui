import json
import time
from pathlib import Path

from musubi_gui.acceptance import prepare
from musubi_gui.runs import RunService


def wait(service: RunService, run_id: str, status: str) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == status:
            return run
        if run["status"] == "failed":
            raise AssertionError(run)
        time.sleep(0.05)
    raise AssertionError(service.get(run_id))


def test_h3_preparation_freezes_audio_and_keeps_guidance_stages(tmp_path: Path) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    service = RunService(Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        job = next(job for job in fixture["jobs"] if job["family"] == "h3")
        run = service.prepare(job["id"])
        prepared = wait(service, run["id"], "prepared")
        audio = prepared["manifest"]["items"][0]["audio"]
        assert audio["source"] == "sidecar"
        assert Path(audio["snapshot_path"]).read_bytes() == Path(audio["audio_path"]).read_bytes()
        assert prepared["provenance"]["audio_coverage"]["supervised"] == 1
        args = next(stage["args"] for stage in prepared["stages"] if stage["name"] == "text_cache")
        assert "--uncond_output" in args
        assert Path(prepared["cache"], "uncond.safetensors").is_file()
        frozen = json.loads(Path(prepared["directory"], "inputs.json").read_text(encoding="utf-8"))
        assert frozen["manifest"]["items"][0]["caption"]["text"].endswith("tone plays.")
    finally:
        service.close()

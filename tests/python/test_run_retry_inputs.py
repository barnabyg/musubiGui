import json
import time
from pathlib import Path

import pytest

from musubi_gui.acceptance import prepare
from musubi_gui.runs import RunService


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_retry_seals_its_executed_native_inputs_and_rejects_edits_before_launch(tmp_path: Path, family: str) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        job = next(job for job in fixture["jobs"] if job["family"] == family)
        original = service.prepare(job["id"])
        original_toml = Path(original["directory"], "dataset.toml")
        original_bytes = original_toml.read_bytes()
        service.cancel(original["id"])
        retried = service.retry(original["id"])
        executed_toml = Path(retried["directory"], "dataset.toml")
        assert str(executed_toml) in retried["input_files"]
        if family == "h3":
            metadata = Path(retried["directory"], "dataset.jsonl")
            assert metadata.is_file()
            assert str(metadata) in retried["input_files"]
            assert metadata.as_posix() in executed_toml.read_text(encoding="utf-8")
            source = json.loads(metadata.read_text(encoding="utf-8"))
            assert Path(source["video_path"]).is_file()
            metadata.write_text("{}\n", encoding="utf-8")
        else:
            executed_toml.write_text("invalid edited native configuration", encoding="utf-8")
        service.start_worker()
        deadline = time.monotonic() + 180
        while service.get(retried["id"])["status"] != "failed" and time.monotonic() < deadline:
            time.sleep(0.02)
        rejected = service.get(retried["id"])
        assert rejected["status"] == "failed"
        assert "Frozen dataset inputs changed" in rejected["error"]
        assert rejected["processes"] == []
        assert all(stage["status"] == "pending" for stage in rejected["stages"])
        assert original_toml.read_bytes() == original_bytes
    finally:
        service.close()

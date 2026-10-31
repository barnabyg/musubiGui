import json
import shutil
import time
from pathlib import Path

from helpers import acceptance_workspace

from musubi_gui.datasets import DatasetStore
from musubi_gui.media_probe import inspect
from musubi_gui.runs import RunService


def wait(service: RunService, run_id: str, status: str) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if run["status"] == status:
            return run
        if run["status"] == "failed":
            raise AssertionError(run)
        time.sleep(0.05)
    raise AssertionError(service.get(run_id))


def test_h3_preparation_freezes_audio_and_keeps_guidance_stages(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
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
        native = Path(prepared["directory"], "dataset.toml").read_text(encoding="utf-8")
        assert "video_jsonl_file = " in native and "source_fps" not in native
    finally:
        service.close()


def test_explicit_audio_in_a_video_container_cannot_replace_the_frozen_target_video(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    job = next(job for job in fixture["jobs"] if job["family"] == "h3")
    store = DatasetStore(root)
    item = store.items(job["dataset_id"])[0]
    explicit = Path(item["absolute_path"]).with_name("explicit-audio.mp4")
    shutil.copyfile(tmp_path / "acceptance/sample.mp4", explicit)
    store.set_audio(job["dataset_id"], item["id"], str(explicit))
    service = RunService(root, fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        run = service.prepare(job["id"])
        prepared = wait(service, run["id"], "prepared")
        frozen = prepared["manifest"]["items"][0]
        assert Path(frozen["snapshot_path"]).read_bytes() == Path(item["absolute_path"]).read_bytes()
        assert frozen["snapshot_path"] != frozen["audio"]["snapshot_path"]
        metadata = json.loads(Path(prepared["directory"], "dataset.jsonl").read_text(encoding="utf-8"))
        assert metadata["audio_path"] == frozen["audio"]["snapshot_path"]
    finally:
        service.close()


def test_h3_training_outputs_a_checkpoint_and_decodable_video_with_audio(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    service = RunService(Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite")
    try:
        job = next(job for job in fixture["jobs"] if job["family"] == "h3")
        run = service.prepare(job["id"])
        wait(service, run["id"], "prepared")
        service.start(run["id"])
        completed = wait(service, run["id"], "completed")
        assert completed["checkpoints"]
        sample = Path(completed["directory"], "samples/sample.mp4")
        metadata = inspect(sample, timeline=True)
        assert metadata["frames"] == 124
        assert metadata["audio"]["decoded_seconds"] >= 5
        assert completed["samples"][0]["status"] == "playable"
    finally:
        service.close()

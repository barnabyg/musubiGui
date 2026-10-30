import copy
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from musubi_gui import snapshots
from musubi_gui.acceptance import prepare
from musubi_gui.runs import RunService


def test_version_only_probe_reports_optional_preprocessors_without_initializing_torch() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-B", "musubi_gui/probe.py", "--versions-only"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    report = json.loads(result.stdout.removeprefix("MUSUBI_PROBE="))
    assert set(report["preprocessing_packages"]) == {
        "pillow",
        "numpy",
        "tokenizers",
        "sentencepiece",
        "torchvision",
        "torchaudio",
        "scipy",
        "einops",
    }
    assert "torch_error" not in report
    assert "cuda_available" not in report
    assert set(report["packages"]) == {"torch", "accelerate", "safetensors", "transformers", "av", "musubi-tuner"}


def test_preprocessor_versions_invalidate_cache_and_changed_queued_environment_blocks_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    environment = {
        "python": "3.12.10",
        "platform": "Windows-11",
        "packages": {"torch": "2.13.0"},
        "preprocessing_packages": {"pillow": "12.0", "numpy": "2.4"},
        "cuda": "13.0",
        "cuda_available": True,
        "gpu": {"name": "synthetic", "free_bytes": 100},
    }
    # External selected-environment probe seam; the worker subprocesses are real CPU fixtures.
    monkeypatch.setattr(snapshots, "training_environment", lambda settings, **options: copy.deepcopy(environment))
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        first = service.prepare(fixture["jobs"][0]["id"])
        environment["preprocessing_packages"]["numpy"] = "2.5"
        updated = service.prepare(fixture["jobs"][0]["id"])
        assert updated["stage_keys"] != first["stage_keys"]
        environment["cuda_available"] = False
        environment.pop("gpu")  # The real probe omits GPU details when CUDA is temporarily unavailable.
        assert service.prepare(fixture["jobs"][0]["id"])["id"] == updated["id"]
        service.start_worker()
        # Two real fixture preparations; the same budget as the browser suite's preparation waits.
        deadline = time.monotonic() + 45
        while (
            service.get(first["id"])["status"] != "failed" or service.get(updated["id"])["status"] != "prepared"
        ) and time.monotonic() < deadline:
            time.sleep(0.02)
        rejected = service.get(first["id"])
        assert rejected["status"] == "failed"
        assert "environment changed" in rejected["error"]
        assert rejected["processes"] == []
        assert all(stage["status"] == "pending" for stage in rejected["stages"])
        assert service.get(updated["id"])["status"] == "prepared"
    finally:
        service.close()

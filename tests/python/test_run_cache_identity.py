import copy
from pathlib import Path

import pytest

from musubi_gui import snapshots
from musubi_gui.acceptance import prepare
from musubi_gui.runs import RunService


def test_cache_identity_changes_with_source_or_packages_but_not_volatile_probe_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    environment = {
        "python": "3.12.10",
        "platform": "Windows-11",
        "packages": {"torch": "2.13.0", "av": "18.0"},
        "cuda": "13.0",
        "cuda_available": True,
        "gpu": {"name": "synthetic", "free_bytes": 100},
    }
    # External selected-Python probe boundary: no GPU/model code runs in this regression.
    monkeypatch.setattr(snapshots, "training_environment", lambda settings: copy.deepcopy(environment))
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        job = fixture["jobs"][0]
        first = service.prepare(job["id"])
        script = tmp_path / "acceptance" / "checkout" / "wan_cache_latents.py"
        script.write_text(
            script.read_text(encoding="utf-8") + "\n# changed preprocessing implementation\n", encoding="utf-8"
        )
        changed_source = service.prepare(job["id"])
        assert changed_source["stage_keys"] != first["stage_keys"]
        environment["packages"]["av"] = "19.0"
        changed_package = service.prepare(job["id"])
        assert changed_package["stage_keys"] != changed_source["stage_keys"]
        environment["gpu"]["free_bytes"] = 1
        environment["cuda_available"] = False
        volatile_probe = service.prepare(job["id"])
        assert volatile_probe["stage_keys"] == changed_package["stage_keys"]
    finally:
        service.close()

import json
import os
import subprocess
import sys
from pathlib import Path


def test_reference_failure_writes_unproven_evidence_without_touching_installation(tmp_path: Path) -> None:
    checkout = tmp_path / "existing install"
    checkout.mkdir()
    marker = checkout / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    state = tmp_path / "settings.json"
    output = tmp_path / "outputs"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "checkout": str(checkout),
                "python": sys.executable,
                "output_root": str(output),
                "cache_root": str(tmp_path / "cache"),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.reference", "wan", "--settings", str(state)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    evidence = list(output.glob("wan-*/evidence.json"))
    assert len(evidence) == 1
    report = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert report["support_gate"] == "unresolved"
    assert report["status"] == "blocked"
    assert "compatible model files" in " ".join(report["blockers"])
    assert sorted(p.name for p in checkout.iterdir()) == ["keep.txt"]
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_reference_child_overrides_inherited_hugging_face_cache_locations(tmp_path: Path) -> None:
    state = tmp_path / "settings.json"
    output = tmp_path / "outputs"
    cache = tmp_path / "cache"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "checkout": str(tmp_path / "upstream"),
                "python": sys.executable,
                "output_root": str(output),
                "cache_root": str(cache),
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "MUSUBI_REFERENCE_PROBE": "1",
        "HF_HOME": str(tmp_path / "private-home"),
        "HF_HUB_CACHE": str(tmp_path / "private-hub"),
        "HUGGINGFACE_HUB_CACHE": str(tmp_path / "private-legacy"),
        "HF_XET_CACHE": str(tmp_path / "private-xet"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.reference", "wan", "--settings", str(state)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    report = json.loads(next(output.glob("wan-*/evidence.json")).read_text(encoding="utf-8"))
    actual = report["environment"]["reference_cache_environment"]
    assert all(Path(value).is_relative_to(cache) for value in actual.values())

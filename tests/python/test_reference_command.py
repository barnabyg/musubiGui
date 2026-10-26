import json
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

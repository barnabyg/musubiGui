import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from musubi_gui import snapshots
from musubi_gui.acceptance import prepare
from musubi_gui.datasets import DatasetStore
from musubi_gui.runs import RunService


def test_parallel_prepare_calls_share_one_materialization_and_actual_media_changes_allocate_another(
    tmp_path: Path,
) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        job = fixture["jobs"][0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: service.prepare(job["id"]), range(2)))
        assert runs[0]["id"] == runs[1]["id"]
        output_root = tmp_path / "acceptance" / "outputs" / "runs"
        cache_root = tmp_path / "acceptance" / "cache" / "runs"
        assert len(list(output_root.iterdir())) == 1
        assert len(list(cache_root.iterdir())) == 1
        # A different backend process must find that same reservation before copying too.
        code = "from pathlib import Path; from musubi_gui.runs import RunService; import sys; s=RunService(Path(sys.argv[1]),fixture_mode=True,autostart=False); print(s.prepare(sys.argv[2])['id'])"
        other = subprocess.run(
            [sys.executable, "-c", code, fixture["state"], job["id"]],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert other.stdout.strip() == runs[0]["id"]
        assert len(list(output_root.iterdir())) == 1
        other_job = fixture["jobs"][1]

        def another_backend(_: int) -> str:
            result = subprocess.run(
                [sys.executable, "-c", code, fixture["state"], other_job["id"]],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return result.stdout.strip()

        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_backends = list(pool.map(another_backend, range(2)))
        assert concurrent_backends[0] == concurrent_backends[1]
        assert len(list(output_root.iterdir())) == 2
        item = DatasetStore(Path(fixture["state"])).items(job["dataset_id"])[0]
        source = Path(item["absolute_path"])
        source.write_bytes(source.read_bytes() + b"\nactual media hash changed\n")
        changed = service.prepare(job["id"])
        assert changed["id"] != runs[0]["id"]
        assert len(list(output_root.iterdir())) == 3
        assert changed["manifest"]["items"][0]["sha256"] != runs[0]["manifest"]["items"][0]["sha256"]
    finally:
        service.close()


def test_failed_materialization_keeps_its_source_manifest_and_releases_preparation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = prepare(tmp_path / "acceptance", "fixture", None)
    service = RunService(
        Path(fixture["state"]), fixture_mode=True, device_path=tmp_path / "device.sqlite", autostart=False
    )
    try:
        with monkeypatch.context() as patch:
            # Filesystem boundary failure happens after the immutable source identity is known.
            def fail_copy(source: Path, target: Path) -> None:
                before_copy = json.loads((target.parent.parent / "snapshot.json").read_text(encoding="utf-8"))
                assert before_copy["status"] == "materializing"
                assert before_copy["manifest"]["items"][0]["sha256"]
                assert before_copy["provenance"]["checkout"]["source_digest"]
                raise OSError("fixture storage failure")

            patch.setattr(snapshots.shutil, "copyfile", fail_copy)
            with pytest.raises(OSError, match="fixture storage failure"):
                service.prepare(fixture["jobs"][0]["id"])
        output_root = tmp_path / "acceptance" / "outputs" / "runs"
        incomplete = next(output_root.iterdir())
        record = json.loads((incomplete / "snapshot.json").read_text(encoding="utf-8"))
        assert record["status"] == "incomplete"
        assert record["manifest"]["items"][0]["sha256"]
        assert record["provenance"]["checkout"]["source_digest"]
        assert service.prepare(fixture["jobs"][0]["id"])["status"] == "pending_preparation"
        assert (incomplete / "snapshot.json").is_file()
    finally:
        service.close()

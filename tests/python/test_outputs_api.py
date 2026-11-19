import json
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

import av
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from musubi_gui.outputs import download_name, output_router
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings, save_settings


def output_client(root: Path) -> tuple[TestClient, RunService]:
    service = RunService(root, device_path=root / "device.sqlite3", autostart=False)
    app = FastAPI()
    app.include_router(output_router(service))
    return TestClient(app), service


def submit_run(
    service: RunService, root: Path, token: str, family: str = "wan", caption: str = "A red square"
) -> dict[str, object]:
    settings = Settings(output_root=str(root / "output"), cache_root=str(root / "cache"))
    save_settings(root, settings)
    return service.submit(
        ExecutionPlan(
            job={"id": "job-one", "name": token, "family": family, "revision": 1, "config": {"rank": 8}},
            settings=settings,
            manifest={"items": [{"id": "item-one", "path": "square.mp4", "sha256": "a", "caption": {"text": caption}}]},
            directory=root / "output" / token,
            cache=root / "cache" / token,
            stages=[],
            fingerprint=token,
            provenance={
                "upstream_revision": "revision-" + token,
                "models": {"dit": {"header_sha256": "model-" + token}},
            },
        )
    )


def checkpoint(file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps(
        {"__metadata__": {"ss_steps": "2"}, "lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    ).encode()
    header += b" " * (-len(header) % 8)
    file.write_bytes(len(header).to_bytes(8, "little") + header + struct.pack("<f", 1.0))


@pytest.mark.parametrize(
    "status",
    [
        "pending_preparation",
        "preparing",
        "prepared",
        "pending_training",
        "training",
        "generating",
        "stopping",
        "orphaned",
        "uncertain",
        "completed",
        "failed",
        "stopped",
        "cancelled",
        "interrupted",
    ],
)
@pytest.mark.parametrize("has_low_checkpoint", [False, True])
def test_paired_outputs_warn_about_missing_experts_only_after_the_run_finishes(
    tmp_path: Path, status: str, has_low_checkpoint: bool
) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "new-pair")
    stored = service.store.get(str(run["id"]))
    stored["job"]["config"]["recipe"] = "alternating"
    stored["status"] = status
    if has_low_checkpoint:
        file = tmp_path / "output" / "new-pair" / "low.safetensors"
        checkpoint(file)
        stored["expert_states"] = {"low": {"checkpoint": {"path": str(file), "steps": 2}}}
    service.store.update(str(run["id"]), lambda record: record.update(stored))
    result = client.get(f"/api/runs/{run['id']}/outputs").json()
    assert result["lora_pair"] is not None
    assert bool(result["warnings"]) == (status in {"completed", "failed", "stopped", "cancelled", "interrupted"})


def test_outputs_keep_run_association_checkpoint_metadata_and_missing_history_after_restart(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    file = tmp_path / "output" / "first" / "trained.safetensors"
    checkpoint(file)
    response = client.get(f"/api/runs/{run['id']}/outputs")
    assert response.status_code == 200
    output = response.json()["outputs"][0]
    assert output["run_id"] == run["id"]
    assert output["status"] == "available"
    assert output["metadata"]["checkpoint"]["ss_steps"] == "2"
    assert output["metadata"]["training_state"] is False
    assert client.get(output["file_url"] + "?download=true").content == file.read_bytes()
    file.unlink()
    restarted, _ = output_client(tmp_path)
    remembered = restarted.get(f"/api/runs/{run['id']}/outputs").json()["outputs"][0]
    assert remembered["id"] == output["id"]
    assert remembered["status"] == "missing"
    assert restarted.get(output["file_url"]).status_code == 404


def test_downloaded_checkpoints_are_named_after_the_job_and_files_stay_unchanged(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "WAN acceptance 1")
    checkpoints = tmp_path / "output" / "WAN acceptance 1"
    checkpoint(checkpoints / "wan-abc.safetensors")
    checkpoint(checkpoints / "wan-abc-step00000100.safetensors")
    outputs = {item["name"]: item for item in client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]}

    def disposition(name: str) -> str:
        response = client.get(outputs[name]["file_url"] + "?download=true")
        assert response.status_code == 200
        return response.headers["content-disposition"]

    assert "filename*=utf-8''WAN%20acceptance%201-r1.safetensors" in disposition("wan-abc.safetensors")
    assert "WAN%20acceptance%201-r1-step100.safetensors" in disposition("wan-abc-step00000100.safetensors")
    assert (checkpoints / "wan-abc.safetensors").is_file()
    assert outputs["wan-abc.safetensors"]["path"] == "wan-abc.safetensors"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Plain name", "Plain name-r3.safetensors"),
        ('a/b\\c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j-r3.safetensors"),
        ("../../escape", "_.._escape-r3.safetensors"),
        ("CON", "_CON-r3.safetensors"),
        ("aux.draft", "_aux.draft-r3.safetensors"),
        ("Console", "Console-r3.safetensors"),
        ("  trailing. ", "trailing-r3.safetensors"),
        ("...", "job-r3.safetensors"),
        ("", "job-r3.safetensors"),
        ("雪と音", "雪と音-r3.safetensors"),
        ("x" * 300, "x" * 120 + "-r3.safetensors"),
    ],
)
def test_download_names_are_safe_on_windows(name: str, expected: str) -> None:
    assert download_name({"name": name, "revision": 3}, Path("wan-id.safetensors")) == expected


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "-step00000010",
        "-high-phase00000020",
        "-high-phase00000020-step00000010",
        "-high-step00000010",
        "-low-step00000010",
    ],
)
def test_job_names_do_not_pass_for_paired_checkpoint_suffixes(suffix: str) -> None:
    job = {"name": "Job-low-phase123", "revision": 2}
    expected = suffix.replace("00000020", "20").replace("00000010", "10")
    assert (
        download_name(job, Path(f"Job-low-phase123-r2{suffix}.safetensors"))
        == f"Job-low-phase123-r2{expected}.safetensors"
    )


def test_saved_state_weights_are_excluded_from_the_run_outputs(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    checkpoint(Path(run["directory"]) / "state" / "lora-state" / "model.safetensors")
    assert client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"] == []


def test_non_ascii_job_names_download_with_an_encoded_filename(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "雪と音")
    checkpoint(tmp_path / "output" / "雪と音" / "wan-abc.safetensors")
    output = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"][0]
    disposition = client.get(output["file_url"] + "?download=true").headers["content-disposition"]
    assert disposition == "attachment; filename*=utf-8''%E9%9B%AA%E3%81%A8%E9%9F%B3-r1.safetensors"


def test_samples_keep_their_own_download_names(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "named")
    samples = tmp_path / "output" / "named" / "samples"
    samples.mkdir(parents=True)
    Image.new("RGB", (4, 4)).save(samples / "still.png")
    output = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"][0]
    assert 'filename="still.png"' in client.get(output["file_url"] + "?download=true").headers["content-disposition"]


def test_output_delivery_rechecks_current_root_before_serving_a_known_output(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    file = tmp_path / "output" / "first" / "trained.safetensors"
    checkpoint(file)
    output = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"][0]
    save_settings(tmp_path, Settings(output_root=str(tmp_path / "other-output")))
    assert client.get(output["file_url"]).status_code == 403
    assert file.exists()


def test_comparison_reports_changed_controls_captions_models_and_upstream(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    first = submit_run(service, tmp_path, "first")
    second = submit_run(service, tmp_path, "second", "h3", "A blue square")
    compared = client.get(f"/api/runs/{first['id']}/compare/{second['id']}")
    assert compared.status_code == 200
    paths = [change["path"] for change in compared.json()["changes"]]
    assert "job.family" in paths
    assert "dataset.items.item-one.caption.text" in paths
    assert "provenance.models.dit.header_sha256" in paths
    assert "provenance.upstream_revision" in paths
    assert compared.json()["quality_ranking"] is None


def test_empty_and_truncated_checkpoints_are_visible_without_claiming_complete_state(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    directory = tmp_path / "output" / "first"
    (directory / "empty.safetensors").touch()
    checkpoint(directory / "short.safetensors")
    data = (directory / "short.safetensors").read_bytes()
    (directory / "short.safetensors").write_bytes(data[:-2])
    outputs = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]
    assert [output["status"] for output in outputs] == ["incomplete", "incomplete"]


def media_samples(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "blue").save(directory / "still.png")
    with wave.open(str(directory / "tone.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    with av.open(str(directory / "motion.mp4"), "w") as container:
        stream: Any = container.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        for _ in range(24):
            frame = av.VideoFrame(64, 64, "yuv420p")
            for plane in frame.planes:
                plane.update(bytes([100]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_media_outputs_are_decoded_and_file_ranges_support_audio_video_playback(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    media_samples(tmp_path / "output" / "first" / "samples")
    outputs = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]
    assert {entry["kind"] for entry in outputs} == {"image", "video", "audio"}
    assert all(entry["status"] == "available" for entry in outputs)
    video = next(entry for entry in outputs if entry["kind"] == "video")
    response = client.get(video["file_url"], headers={"range": "bytes=0-31"})
    assert response.status_code == 206
    assert len(response.content) == 32
    audio = next(entry for entry in outputs if entry["kind"] == "audio")
    assert audio["metadata"]["audio"]["sample_rate"] == 24000


def test_h3_video_without_audio_is_reported_as_incomplete(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first", "h3")
    media_samples(tmp_path / "output" / "first" / "samples")
    outputs = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]
    video = next(entry for entry in outputs if entry["kind"] == "video")
    assert video["status"] == "incomplete"
    assert "no decodable audio" in video["error"]


def test_file_endpoint_rejects_arbitrary_paths_and_another_runs_output_identity(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    first = submit_run(service, tmp_path, "first")
    second = submit_run(service, tmp_path, "second")
    checkpoint(tmp_path / "output" / "first" / "trained.safetensors")
    output = client.get(f"/api/runs/{first['id']}/outputs").json()["outputs"][0]
    assert client.get(f"/api/runs/{second['id']}/outputs/{output['id']}/file").status_code == 404
    assert client.get(f"/api/runs/{first['id']}/outputs/..%2F..%2Fsettings.json/file").status_code in {404, 403}
    assert (
        client.get(f"/api/runs/{first['id']}/outputs/arbitrary/file?path={tmp_path / 'settings.json'}").status_code
        == 404
    )


def test_output_folder_junction_cannot_deliver_files_outside_the_run(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "first")
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGB", (8, 8), "red").save(outside / "private.png")
    link = tmp_path / "output" / "first" / "samples"

    def quote(value: Path) -> str:
        return str(value).replace("'", "''")

    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$null = New-Item -ItemType Junction -Path '{quote(link)}' -Target '{quote(outside)}'",
        ],
        check=True,
        capture_output=True,
    )
    outputs = client.get(f"/api/runs/{run['id']}/outputs").json()["outputs"]
    assert len(outputs) == 1
    assert outputs[0]["status"] == "blocked"
    assert client.get(f"/api/runs/{run['id']}/outputs/{outputs[0]['id']}/file").status_code == 403
    assert (outside / "private.png").is_file()


def preview_image(directory: Path, step: int) -> None:
    folder = directory / "samples" / "sample"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(folder / f"lora_{step:06d}_00_20261116120000.png")


def chain_of_three(client: TestClient, service: RunService, root: Path) -> list[dict[str, Any]]:
    """A training stopped at 120, resumed until it stopped at 250 and resumed again until 400."""
    first = submit_run(service, root, "first")
    second = submit_run(service, root, "second")
    third = submit_run(service, root, "third")
    details = [
        {"status": "stopped"},
        {"status": "stopped", "resume_of": first["id"], "segment": {"step_offset": 120, "target_steps": 300}},
        {"status": "completed", "resume_of": second["id"], "segment": {"step_offset": 250, "target_steps": 400}},
    ]
    runs = []
    for run, change in zip([first, second, third], details):
        runs.append(service.store.update(str(run["id"]), lambda record: record.update(change)))
    first_dir, second_dir, third_dir = (Path(run["directory"]) for run in runs)
    for step in (50, 100):
        preview_image(first_dir, step)
    checkpoint(first_dir / "lora-step00000120.safetensors")
    for step in (150, 200, 250):
        preview_image(second_dir, step)
    checkpoint(second_dir / "lora-step00000200.safetensors")
    for step in (300, 350, 400):
        preview_image(third_dir, step)
    checkpoint(third_dir / "lora.safetensors")
    return runs


def test_a_chain_merges_its_segments_outputs_in_global_step_order(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    first, second, third = chain_of_three(client, service, tmp_path)

    for viewed in (first, second, third):
        merged = client.get(f"/api/runs/{viewed['id']}/outputs", params={"chain": True}).json()
        assert merged["run_id"] == viewed["id"]
        assert merged["latest"] == third["id"]
        assert merged["status"] == "completed"
        assert [segment["run_id"] for segment in merged["segments"]] == [first["id"], second["id"], third["id"]]
        previews = [entry for entry in merged["outputs"] if entry["kind"] == "image"]
        assert [entry["step"] for entry in previews] == [50, 100, 150, 200, 250, 300, 350, 400]
        assert [entry["metadata"]["preview"]["step"] for entry in previews] == [50, 100, 150, 200, 250, 300, 350, 400]
        assert previews[0]["segment"]["position"] == 1 and previews[-1]["segment"]["position"] == 3
        # Each file is still served by its own Run.
        assert previews[0]["file_url"].startswith(f"/api/runs/{first['id']}/")
        assert client.get(previews[0]["file_url"]).status_code == 200
        checkpoints = [(entry["step"], entry["name"]) for entry in merged["outputs"] if entry["kind"] == "checkpoint"]
        assert checkpoints == [
            (120, "lora-step00000120.safetensors"),
            (200, "lora-step00000200.safetensors"),
            (400, "lora.safetensors"),
        ]
    # Without `chain`, a segment lists its own files as before.
    own = client.get(f"/api/runs/{second['id']}/outputs").json()
    assert {entry["run_id"] for entry in own["outputs"]} == {second["id"]}
    assert "segments" not in own


def test_a_single_run_lists_the_same_outputs_with_or_without_chain(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    run = submit_run(service, tmp_path, "only")
    checkpoint(Path(str(run["directory"])) / "lora.safetensors")
    plain = client.get(f"/api/runs/{run['id']}/outputs").json()
    chained = client.get(f"/api/runs/{run['id']}/outputs", params={"chain": True}).json()
    assert [entry["path"] for entry in chained["outputs"]] == [entry["path"] for entry in plain["outputs"]]
    assert chained["latest"] == run["id"] and len(chained["segments"]) == 1
    assert chained["note"] == plain["note"]


def test_a_legacy_segment_orders_its_own_step_names_after_its_saved_step(tmp_path: Path) -> None:
    client, service = output_client(tmp_path)
    first, second = submit_run(service, tmp_path, "first"), submit_run(service, tmp_path, "second")
    service.store.update(str(first["id"]), lambda record: record.update(status="stopped"))
    service.store.update(
        str(second["id"]),
        lambda record: record.update(
            status="completed",
            resume_of=first["id"],
            segment={"additional_steps": 100, "optimizer_step_before": 100, "counter_resets": True},
        ),
    )
    preview_image(Path(str(first["directory"])), 80)
    # Restored before continuous numbering: its step 50 is the training's step 150.
    preview_image(Path(str(second["directory"])), 50)
    merged = client.get(f"/api/runs/{second['id']}/outputs", params={"chain": True}).json()
    assert [(entry["step"], entry["segment"]["position"]) for entry in merged["outputs"]] == [(80, 1), (150, 2)]

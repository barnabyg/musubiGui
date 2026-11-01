"""Sections across several datasets: the reviewed TOML is the TOML that runs."""

import hashlib
import json
import shutil
import tomllib
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import PNG, acceptance_workspace, wait_for
from PIL import Image

from musubi_gui.acceptance import synthetic_audio, synthetic_video
from musubi_gui.app import create_app
from musubi_gui.jobs import Job, JobDataset, build_stages, smoke_config
from musubi_gui.runs import RunService
from musubi_gui.settings import Settings


class Workspace:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.acceptance = acceptance_workspace(tmp_path)
        self.state = Path(self.acceptance["state"])
        self.folder = tmp_path / "acceptance" / "datasets"
        assets = tmp_path / "assets"
        assets.mkdir()
        self.client = TestClient(create_app(self.state, assets, fixture_mode=True, device_path=tmp_path / "d.sqlite"))

    def images(self, name: str, count: int = 2) -> str:
        folder = self.folder / name
        folder.mkdir()
        for index in range(count):
            (folder / f"still{index}.png").write_bytes(PNG + bytes([index]))
            (folder / f"still{index}.txt").write_text(f"Still {index}.", encoding="utf-8")
        return self.register(name, folder)

    def videos(self, name: str, *clips: tuple[str, int, int], audio: bool = False) -> str:
        folder = self.folder / name
        folder.mkdir()
        for clip, frames, rate in clips:
            synthetic_video(folder / clip, frames, rate=Fraction(rate))
            (folder / clip).with_suffix(".txt").write_text(f"Clip {clip} of {name}.", encoding="utf-8")
            if audio:
                synthetic_audio((folder / clip).with_suffix(".wav"), frames)
        return self.register(name, folder)

    def register(self, name: str, folder: Path) -> str:
        return str(self.client.post("/api/datasets", json={"name": name, "path": str(folder)}).json()["id"])

    def job(self, family: str, *rows: dict[str, Any], **config: Any) -> dict[str, Any]:
        merged = {**smoke_config(family).model_dump(), **config}
        body = {"name": "Several", "family": family, "datasets": list(rows), "config": merged}
        response = self.client.post("/api/jobs", json=body)
        assert response.status_code == 200, response.text
        created: dict[str, Any] = response.json()
        return created

    def review(self, job: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"/api/jobs/{job['id']}/review", json={})
        assert response.status_code == 200, response.text
        review: dict[str, Any] = response.json()
        return review

    def service(self) -> RunService:
        return RunService(self.state, fixture_mode=True, device_path=self.tmp_path / "service.sqlite")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def wan_mixed_job(workspace: Workspace) -> dict[str, Any]:
    stills = workspace.images("stills")
    clips = workspace.videos("clips", ("thirty.mp4", 60, 30), ("native.mp4", 33, 16))
    return workspace.job("wan", {"dataset_id": stills, "repeats": 4}, {"dataset_id": clips}, frames=17, repeats=2)


def test_wan_datasets_get_separate_sections_caches_and_their_own_repeats(workspace: Workspace) -> None:
    review = workspace.review(wan_mixed_job(workspace))
    assert review["valid"] is True, review["errors"]
    native = tomllib.loads(review["dataset_toml"])
    assert native["general"]["num_repeats"] == 2
    [images, native_rate, thirty] = native["datasets"]
    snapshot, cache = review["paths"]["snapshot"].replace("\\", "/"), review["paths"]["cache"].replace("\\", "/")
    assert images["image_directory"] == f"{snapshot}/media/set-1"
    assert images["cache_directory"] == f"{cache}/dataset-1-images"
    assert images["num_repeats"] == 4
    assert native_rate["video_directory"] == f"{snapshot}/media/set-2/rate-1"
    assert native_rate["cache_directory"] == f"{cache}/dataset-2-rate-1"
    assert native_rate["source_fps"] == 16.0
    assert "num_repeats" not in native_rate
    assert thirty["cache_directory"] == f"{cache}/dataset-2-rate-2"
    assert thirty["source_fps"] == 30.0


def test_review_toml_matches_the_materialized_file_after_path_substitution(workspace: Workspace) -> None:
    job = wan_mixed_job(workspace)
    review = workspace.review(job)
    service = workspace.service()
    try:
        run = service.prepare(job["id"])
        prepared = wait_for(service, run["id"], "prepared", device_released=True)
    finally:
        service.close()
    executed = Path(prepared["directory"], "dataset.toml").read_text(encoding="utf-8")
    expected = (
        review["dataset_toml"]
        .replace(review["paths"]["snapshot"].replace("\\", "/"), Path(prepared["directory"]).as_posix())
        .replace(review["paths"]["cache"].replace("\\", "/"), Path(prepared["cache"]).as_posix())
    )
    assert executed == expected
    native = tomllib.loads(executed)
    assert len({section["cache_directory"] for section in native["datasets"]}) == 3
    for section in native["datasets"]:
        folder = Path(section.get("video_directory", section.get("image_directory")))
        assert [file for file in folder.iterdir() if file.suffix in {".png", ".mp4"}]
    # Every included item, from every section, has its cache artifacts.
    latent = next(stage for stage in prepared["stages"] if stage["name"] == "latent_cache")
    assert {Path(file["path"]).parts[0] for file in latent["artifacts"]} == {
        "dataset-1-images",
        "dataset-2-rate-1",
        "dataset-2-rate-2",
    }
    assert len(latent["artifacts"]) == 4


def test_h3_with_two_video_datasets_prepares_trains_and_retries_with_both_jsonl_files(workspace: Workspace) -> None:
    first = workspace.videos("first", ("a.mp4", 124, 24), audio=True)
    second = workspace.videos("second", ("a.mp4", 124, 24), ("b.mp4", 124, 24), audio=True)
    job = workspace.job("h3", {"dataset_id": first}, {"dataset_id": second, "repeats": 3})
    review = workspace.review(job)
    assert review["valid"] is True, review["errors"]
    assert review["audio_coverage"]["included"] == 3
    assert review["audio_coverage"]["supervised"] == 3
    service = workspace.service()
    try:
        run = service.prepare(job["id"], start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        assert completed["checkpoints"]
        directory = Path(completed["directory"])
        native = tomllib.loads((directory / "dataset.toml").read_text(encoding="utf-8"))
        assert [section["video_jsonl_file"] for section in native["datasets"]] == [
            (directory / "dataset-1.jsonl").as_posix(),
            (directory / "dataset-2.jsonl").as_posix(),
        ]
        assert [section.get("num_repeats") for section in native["datasets"]] == [None, 3]
        lines = (directory / "dataset-2.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert all(Path(json.loads(line)["audio_path"]).is_file() for line in lines)
        expected = tomllib.loads(
            review["dataset_toml"]
            .replace(review["paths"]["snapshot"].replace("\\", "/"), directory.as_posix())
            .replace(review["paths"]["cache"].replace("\\", "/"), Path(completed["cache"]).as_posix())
        )
        assert native == expected

        service.store.update(run["id"], lambda record: record.update(status="failed"))
        retried = service.retry(run["id"])
        retry_directory = Path(retried["directory"])
        retry_native = tomllib.loads((retry_directory / "dataset.toml").read_text(encoding="utf-8"))
        for number, section in enumerate(retry_native["datasets"], 1):
            metadata = retry_directory / f"dataset-{number}.jsonl"
            assert section["video_jsonl_file"] == metadata.as_posix()
            assert str(metadata) in retried["input_files"]
            assert section["cache_directory"].startswith(Path(retried["cache"]).as_posix())
        wait_for(service, retried["id"], "prepared", device_released=True)
        service.start(retried["id"])
        assert wait_for(service, retried["id"], "completed", device_released=True)["checkpoints"]
    finally:
        service.close()


def latent_stage(service: RunService, job_id: str) -> dict[str, Any]:
    run = service.prepare(job_id)
    prepared = wait_for(service, run["id"], "prepared", device_released=True)
    stage: dict[str, Any] = next(stage for stage in prepared["stages"] if stage["name"] == "latent_cache")
    return {**stage, "key": prepared["stage_keys"]["latent_cache"]}


def test_cache_reuse_follows_the_section_layout(workspace: Workspace) -> None:
    stills = workspace.images("stills")
    clips = workspace.videos("clips", ("a.mp4", 33, 16))
    service = workspace.service()
    try:
        first = latent_stage(service, workspace.job("wan", {"dataset_id": stills}, {"dataset_id": clips})["id"])
        assert first["status"] == "completed"
        same = latent_stage(service, workspace.job("wan", {"dataset_id": stills}, {"dataset_id": clips})["id"])
        assert same["status"] == "reused"
        assert same["key"] == first["key"]
        swapped = latent_stage(service, workspace.job("wan", {"dataset_id": clips}, {"dataset_id": stills})["id"])
        assert swapped["status"] == "completed"
        assert swapped["key"] != first["key"]
        repeated = latent_stage(
            service, workspace.job("wan", {"dataset_id": stills, "repeats": 3}, {"dataset_id": clips})["id"]
        )
        assert repeated["status"] == "completed"
        assert repeated["key"] != first["key"]
    finally:
        service.close()


def test_one_dataset_keeps_its_layout_and_row_repeats_only_change_its_section(workspace: Workspace) -> None:
    clips = workspace.videos("clips", ("a.mp4", 33, 16))
    plain = workspace.review(workspace.job("wan", {"dataset_id": clips}))
    repeated = workspace.review(workspace.job("wan", {"dataset_id": clips, "repeats": 5}))
    [section] = tomllib.loads(plain["dataset_toml"])["datasets"]
    assert section["video_directory"].endswith("/media")
    assert section["cache_directory"].endswith("/dataset")
    assert "num_repeats" not in section
    assert tomllib.loads(repeated["dataset_toml"])["datasets"][0]["num_repeats"] == 5


def test_review_warns_about_identical_files_and_names_each_items_dataset(workspace: Workspace) -> None:
    first = workspace.videos("first", ("same.mp4", 33, 16))
    second_folder = workspace.folder / "second"
    second_folder.mkdir()
    shutil.copyfile(workspace.folder / "first" / "same.mp4", second_folder / "copy.mp4")
    second = workspace.register("second", second_folder)
    review = workspace.review(workspace.job("wan", {"dataset_id": first}, {"dataset_id": second}))
    assert any(
        warning.startswith("Identical files") and 'same.mp4 in "first"' in warning and 'copy.mp4 in "second"' in warning
        for warning in review["warnings"]
    )
    # copy.mp4 has no caption in its own dataset, and the message says which dataset that is.
    assert 'Dataset "second": copy.mp4: no caption sidecar; the frozen caption will be empty.' in review["warnings"]


def test_review_prefixes_item_errors_because_relative_paths_can_collide(workspace: Workspace) -> None:
    good = workspace.videos("good", ("clip.mp4", 33, 16))
    short = workspace.videos("short", ("clip.mp4", 3, 16))
    review = workspace.review(workspace.job("wan", {"dataset_id": good}, {"dataset_id": short}, frames=17))
    assert any(error.startswith('Dataset "short": clip.mp4:') for error in review["errors"])
    assert not any(error.startswith('Dataset "good"') for error in review["errors"])


def test_identical_files_in_one_dataset_are_not_a_warning(workspace: Workspace) -> None:
    clips = workspace.videos("clips", ("a.mp4", 33, 16))
    shutil.copyfile(workspace.folder / "clips" / "a.mp4", workspace.folder / "clips" / "b.mp4")
    assert workspace.client.post(f"/api/datasets/{clips}/scan", json={}).is_success
    other = workspace.images("stills")
    review = workspace.review(workspace.job("wan", {"dataset_id": clips}, {"dataset_id": other}))
    assert not any(warning.startswith("Identical files") for warning in review["warnings"])


def stage_args(run: dict[str, Any], name: str) -> list[str]:
    stage: list[str] = next(stage["args"] for stage in run["stages"] if stage["name"] == name)
    return stage


@pytest.mark.parametrize("combined", [True, False], ids=["combined", "image-only"])
def test_h3_images_review_prepare_train_sample_and_retry_with_the_right_flags(
    workspace: Workspace, combined: bool
) -> None:
    stills = workspace.images("Susan images")
    rows: list[dict[str, Any]] = [{"dataset_id": stills}]
    if combined:
        rows.append({"dataset_id": workspace.videos("Susan videos", ("a.mp4", 124, 24), audio=True)})
    job = workspace.job("h3", *rows)
    review = workspace.review(job)
    assert review["valid"] is True, review["errors"]
    for name in ["latent_cache", "text_cache", "train"]:
        assert "--one_frame" in stage_args(review, name)
    assert "--one_frame" not in stage_args(review, "generate")
    assert ("--video_only" in stage_args(review, "train")) is not combined
    assert "--video_only" not in stage_args(review, "latent_cache")
    warnings = " ".join(review["warnings"])
    assert "experimental" in warnings and "unvalidated" in warnings and "85,980" in warnings
    assert "absence of sound" in warnings and "every other model" in warnings
    # Audio coverage counts the clips only: an image is never missing audio.
    assert review["audio_coverage"]["included"] == (1 if combined else 0)
    assert review["audio_coverage"]["missing"] == 0
    assert not any("audio" in error for error in review["errors"])

    service = workspace.service()
    try:
        run = service.prepare(job["id"], start=True)
        completed = wait_for(service, run["id"], "completed", device_released=True)
        directory = Path(completed["directory"])
        native = tomllib.loads((directory / "dataset.toml").read_text(encoding="utf-8"))
        images = native["datasets"][0]
        assert images["image_directory"].endswith("/media/set-1" if combined else "/media")
        assert images["cache_directory"].endswith("/dataset-1-images" if combined else "/dataset-images")
        assert "video_jsonl_file" not in images
        if combined:
            lines = (directory / "dataset-2.jsonl").read_text(encoding="utf-8").splitlines()
            assert [Path(json.loads(line)["video_path"]).suffix for line in lines] == [".mp4"]
            assert native["datasets"][1]["cache_directory"] != images["cache_directory"]
        # The sample stays a video with audio.
        assert completed["samples"][0]["metadata"]["frames"] == 124
        assert completed["samples"][0]["metadata"]["audio"]
        assert Path(completed["cache"], "uncond.safetensors").is_file()

        service.store.update(run["id"], lambda record: record.update(status="failed"))
        retried = service.retry(run["id"])
        assert "--one_frame" in stage_args(retried, "train")
        wait_for(service, retried["id"], "prepared", device_released=True)
        service.start(retried["id"])
        assert wait_for(service, retried["id"], "completed", device_released=True)["checkpoints"]
    finally:
        service.close()


def test_a_mixed_folder_works_for_h3_with_its_clips_alone_in_the_jsonl(workspace: Workspace) -> None:
    mixed = workspace.videos("Mixed", ("a.mp4", 124, 24), audio=True)
    (workspace.folder / "Mixed" / "still.png").write_bytes(PNG)
    assert workspace.client.post(f"/api/datasets/{mixed}/scan", json={}).is_success
    job = workspace.job("h3", {"dataset_id": mixed})
    review = workspace.review(job)
    assert review["valid"] is True, review["errors"]
    assert "--video_only" not in stage_args(review, "train")
    service = workspace.service()
    try:
        run = wait_for(service, service.prepare(job["id"])["id"], "prepared", device_released=True)
    finally:
        service.close()
    records = Path(run["directory"], "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    assert [Path(json.loads(line)["video_path"]).suffix for line in records] == [".mp4"]


def test_a_video_only_h3_jobs_arguments_match_a_fixed_regression_value() -> None:
    names = ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]
    settings = Settings(
        checkout="checkout", python="python.exe", models={name: f"models/{name}.safetensors" for name in names}
    )
    job = Job(
        id="f" * 32, revision=1, name="n", family="h3", datasets=[JobDataset(dataset_id="d")], config=smoke_config("h3")
    )
    stages = build_stages(job, settings, Path("run"), Path("cache"), {"video"})
    text = json.dumps([[stage.name, stage.args] for stage in stages])
    # Computed before H3 accepted images. The paths use this platform's separators; the suite runs on Windows.
    expected = "e272a88e6eee7d18141e853b952f88ec5c0aea54fab1995b6724e3d4dbb97c96"  # pragma: allowlist secret
    assert hashlib.sha256(text.encode()).hexdigest() == expected


def test_the_h3_fixture_refuses_an_image_section_without_the_one_frame_flag(workspace: Workspace) -> None:
    stills = workspace.images("stills")
    job = workspace.job("h3", {"dataset_id": stills})
    # Not started until the record is changed, so the stage can't run with the flag first.
    service = RunService(
        workspace.state, fixture_mode=True, device_path=workspace.tmp_path / "s.sqlite", autostart=False
    )
    try:
        run = service.prepare(job["id"])

        def without_flag(record: dict[str, Any]) -> None:
            for stage in record["stages"]:
                stage["args"] = [arg for arg in stage["args"] if arg != "--one_frame"]

        service.store.update(run["id"], without_flag)
        service.start_worker()
        failed = wait_for(service, run["id"], "failed", device_released=True)
        assert "latent_cache exited 2" in failed["error"]
    finally:
        service.close()


def wide_images(workspace: Workspace, name: str, size: tuple[int, int]) -> str:
    folder = workspace.folder / name
    folder.mkdir()
    for index in range(2):
        Image.new("RGB", size, (index, 0, 0)).save(folder / f"still{index}.png")
        (folder / f"still{index}.txt").write_text("A still.", encoding="utf-8")
    return workspace.register(name, folder)


def shape_warnings(review: dict[str, Any]) -> list[str]:
    return [warning for warning in review["warnings"] if "different shape" in warning]


def test_review_warns_about_images_a_centre_crop_would_reshape_only_with_bucketing_off(workspace: Workspace) -> None:
    square = wide_images(workspace, "Square stills", (64, 64))
    row = {"dataset_id": square}
    [message] = shape_warnings(workspace.review(workspace.job("wan", row, width=832, height=480)))
    assert message.startswith('Dataset "Square stills": 2 images have a different shape from 832×480')
    assert "crops" in message
    assert shape_warnings(workspace.review(workspace.job("wan", row, width=832, height=480, enable_bucket=True))) == []
    assert shape_warnings(workspace.review(workspace.job("wan", row, width=256, height=256))) == []


def test_review_counts_only_included_images_and_treats_a_mixed_folder_like_any_other(workspace: Workspace) -> None:
    mixed = workspace.images("Mixed")
    synthetic_video(workspace.folder / "Mixed" / "clip.mp4", 33, rate=Fraction(16))
    (workspace.folder / "Mixed" / "clip.txt").write_text("A clip.", encoding="utf-8")
    assert workspace.client.post(f"/api/datasets/{mixed}/scan", json={}).is_success
    items = workspace.client.get(f"/api/datasets/{mixed}/items").json()
    excluded = next(item["id"] for item in items if item["name"] == "still0.png")
    body = {"item_ids": [excluded], "excluded": True}
    assert workspace.client.post(f"/api/datasets/{mixed}/exclusions", json=body).is_success
    # The clip is square too, but the warning is about images; the mixed folder itself never warns.
    review = workspace.review(workspace.job("wan", {"dataset_id": mixed}, width=320, height=256))
    [message] = shape_warnings(review)
    assert '"Mixed": 1 image has a different shape' in message
    assert not any("mixed" in text.lower() for text in [*review["warnings"], *review["errors"]] if "Mixed" not in text)


def balance(review: dict[str, Any]) -> list[tuple[str, str, int, int, int, float]]:
    return [
        (row["dataset"], row["kind"], row["items"], row["repeats"], row["samples"], round(row["share"], 4))
        for row in review["sections"]
    ]


def test_samples_and_shares_match_a_hand_worked_example(workspace: Workspace) -> None:
    stills = workspace.images("stills")
    clips = workspace.videos("clips", ("native.mp4", 33, 16), ("thirty.mp4", 90, 30))
    job = workspace.job(
        "wan",
        {"dataset_id": stills, "repeats": 3},
        {"dataset_id": clips},
        frames=17,
        repeats=2,
        frame_extraction="slide",
        frame_stride=8,
    )
    review = workspace.review(job)
    assert review["valid"] is True, review["errors"]
    # 2 images × 3 repeats = 6.
    # native.mp4: 33 frames at 16 fps, windows at 0, 8 and 16 = 3, × the job's 2 repeats = 6.
    # thirty.mp4: 90 frames at 30 fps keep 48 at 16 fps, windows at 0, 8, 16 and 24 = 4, × 2 = 8.
    assert balance(review) == [
        ("stills", "image", 2, 3, 6, 0.3),
        ("clips", "video", 1, 2, 6, 0.3),
        ("clips", "video", 1, 2, 8, 0.4),
    ]
    assert [row["fps"] for row in review["sections"]] == [None, 16.0, 30.0]
    assert sum(row["share"] for row in review["sections"]) == pytest.approx(1)
    assert not any(warning.startswith("Balance:") for warning in review["warnings"])


def test_review_warns_when_one_section_has_most_of_the_samples_but_never_blocks(workspace: Workspace) -> None:
    stills = workspace.images("stills", count=5)
    clips = workspace.videos("clips", ("a.mp4", 33, 16))
    swamped = workspace.review(workspace.job("wan", {"dataset_id": stills, "repeats": 9}, {"dataset_id": clips}))
    assert swamped["valid"] is True, swamped["errors"]
    assert any(warning.startswith('Balance: "stills" (images) has 98%') for warning in swamped["warnings"])
    # Exactly 80% is not over the line.
    level = workspace.review(
        workspace.job("wan", {"dataset_id": stills, "repeats": 4}, {"dataset_id": clips, "repeats": 5})
    )
    assert [row["samples"] for row in level["sections"]] == [20, 5]
    assert not any(warning.startswith("Balance:") for warning in level["warnings"])
    # One section is all of training, which is not an imbalance.
    alone = workspace.review(workspace.job("wan", {"dataset_id": clips}))
    assert [row["share"] for row in alone["sections"]] == [1.0]
    assert not any(warning.startswith("Balance:") for warning in alone["warnings"])

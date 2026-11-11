from pathlib import Path

import pytest

from musubi_gui.disk_estimate import estimate_disk
from musubi_gui.jobs import Job, JobConfig, build_stages, configuration_errors, smoke_config
from musubi_gui.preview import preview_prompt
from musubi_gui.sampling import sample_geometry
from musubi_gui.settings import Settings


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_legacy_samples_inherit_geometry_but_explicit_overrides_are_shared_by_all_sampling(
    family: str, tmp_path: Path
) -> None:
    config = smoke_config(family)
    old = JobConfig.model_validate(config.model_dump(exclude={"sample_width", "sample_height", "sample_frames"}))
    assert sample_geometry(old) == (config.width, config.height, config.frames)
    config = config.model_copy(
        update={"sample_width": 320, "sample_height": 192, "sample_frames": 33 if family == "wan" else 141}
    )
    job = Job(id="job", name="Test", family=family, revision=1, config=config)
    settings = Settings(
        checkout=str(tmp_path),
        python="python",
        models={
            key: key
            for key in ["wan_low", "wan_high", "wan_vae", "wan_t5", "h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]
        },
    )
    stages = build_stages(job, settings, tmp_path / "run", tmp_path / "cache", {"video"})
    args = next(stage.args for stage in stages if stage.name == "generate")
    assert args[args.index("--video_size") + 1 : args.index("--video_size") + 3] == ["192", "320"]
    assert args[args.index("--video_length") + 1] == str(config.sample_frames)
    prompt = preview_prompt(job)[0]
    assert (prompt["width"], prompt["height"], prompt["frame_count"]) == (320, 192, 49 if family == "wan" else 141)
    if family == "wan":
        assert abs(prompt["frame_count"] / 24 - config.sample_frames / 16) <= 2 / 24
        legacy = job.model_copy(update={"config": old})
        assert preview_prompt(legacy)[0]["frame_count"] == old.frames
    assert (config.width, config.height, config.frames) == (old.width, old.height, old.frames)


@pytest.mark.parametrize(
    ("family", "update", "prefix"),
    [
        ("wan", {"sample_width": 63}, "Sample width:"),
        ("h3", {"sample_height": 80}, "Sample height:"),
        ("wan", {"sample_frames": 32}, "Sample duration:"),
        ("h3", {"sample_frames": 1}, "Sample duration:"),
        ("h3", {"sample_frames": 362}, "Sample duration:"),
    ],
)
def test_sample_overrides_are_validated_only_when_sampling_is_enabled(
    family: str, update: dict[str, int], prefix: str
) -> None:
    config = smoke_config(family).model_copy(update=update)
    job = Job(id="job", name="Job", family=family, revision=1, config=config)
    assert any(error.startswith(prefix) for error in configuration_errors(job))
    job.config.sample_enabled = False
    assert not any(error.startswith(prefix) for error in configuration_errors(job))
    job.config.preview_every = 1
    assert any(error.startswith(prefix) for error in configuration_errors(job))


def test_independent_sample_geometry_changes_sample_budget_without_changing_training_cache_budget() -> None:
    config = smoke_config("wan")
    original = estimate_disk("wan", config, [], media_bytes=0)
    updated = estimate_disk(
        "wan", config.model_copy(update={"sample_width": 512, "sample_height": 512}), [], media_bytes=0
    )
    assert updated["samples"] == 4 * original["samples"]
    assert updated["caches"] == original["caches"]


def test_explicit_image_sampling_reserves_uncompressed_bytes_and_legacy_one_frame_keeps_its_output_type(
    tmp_path: Path,
) -> None:
    config = smoke_config("wan").model_copy(update={"frames": 1})
    settings = Settings(
        checkout=str(tmp_path),
        python="python",
        models={key: key for key in ["wan_low", "wan_high", "wan_vae", "wan_t5"]},
    )
    for override, output_type in [(None, "video"), (1, "images")]:
        job = Job(
            id="job", name="Job", revision=1, family="wan", config=config.model_copy(update={"sample_frames": override})
        )
        stage = next(
            stage
            for stage in build_stages(job, settings, tmp_path / "run", tmp_path / "cache", {"image"})
            if stage.name == "generate"
        )
        assert stage.args[stage.args.index("--output_type") + 1] == output_type
    budget = estimate_disk("wan", config.model_copy(update={"sample_frames": 1}), [], media_bytes=0)
    assert budget["samples"] == 4 * 256 * 256 + 4096

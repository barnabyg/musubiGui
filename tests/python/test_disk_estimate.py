from pathlib import Path

import pytest

from musubi_gui.disk_estimate import check_volumes, estimate_disk
from musubi_gui.jobs import JobConfig, default_config
from musubi_gui.settings import Settings

GB = 1000**3


def clip(windows: int | None = 1) -> dict:
    return {"kind": "video", "windows": windows}


def wan_trial_config(**changes: object) -> JobConfig:
    """Run fb66e3d3 in #26, whose output and cache folders were measured."""
    values = {
        "width": 832,
        "height": 480,
        "frames": 33,
        "rank": 16,
        "steps": 200,
        "save_every": 100,
        "save_state": True,
    }
    return default_config("wan").model_copy(update={**values, **changes})


def test_wan_estimate_matches_the_measured_production_trial() -> None:
    estimate = estimate_disk("wan", wan_trial_config(), [clip()] * 28, media_bytes=65_763_007)
    assert estimate["media_snapshot"] == 65_763_007
    # Measured: 50,325,408 latent + 11,130,880 text + 21,508,498 Hugging Face bytes.
    assert estimate["caches"] == pytest.approx(82_964_786, rel=0.01)
    # Measured: three 306,848,376-byte LoRA files and three ~920,958,500-byte state folders before duplicates go.
    assert estimate["saves"] == 3
    assert estimate["checkpoints"] == 920_545_128
    assert estimate["states"] == pytest.approx(2_762_875_496, rel=0.001)
    assert estimate["samples"] >= 949_582
    assert estimate["cache_root"] == estimate["caches"]
    assert estimate["output_root"] == pytest.approx(3.75 * GB, rel=0.01)


def test_h3_estimate_matches_measured_rank_eight_and_both_measured_geometries() -> None:
    config = default_config("h3").model_copy(
        update={"width": 640, "height": 352, "rank": 8, "steps": 20, "save_every": 10, "save_state": True}
    )
    estimate = estimate_disk("h3", config, [clip()] * 20, media_bytes=16_723_061)
    assert estimate["checkpoints"] == 3 * 149_165_288
    assert estimate["states"] == pytest.approx(3 * 447_704_425, rel=0.001)
    # Measured: 63,592,400 latent + 9,838,064 text + 11,505,839 Hugging Face bytes.
    assert estimate["caches"] == pytest.approx(84_936_303, rel=0.01)
    larger = estimate_disk("h3", config.model_copy(update={"width": 832, "height": 480}), [clip()] * 20, media_bytes=0)
    # Measured: 111,899,600 latent bytes at 832x480.
    assert larger["caches"] == pytest.approx(133_243_503, rel=0.01)


def test_saves_scale_the_estimate_and_state_can_be_left_out() -> None:
    long_run = estimate_disk("wan", wan_trial_config(steps=2000), [clip()], media_bytes=0)
    assert long_run["saves"] == 21
    # A final save that is not on the interval adds one save and leaves no duplicate.
    assert estimate_disk("wan", wan_trial_config(steps=250), [clip()], media_bytes=0)["saves"] == 3
    without_state = estimate_disk("wan", wan_trial_config(save_state=False), [clip()], media_bytes=0)
    assert without_state["states"] == 0
    without_sample = estimate_disk("wan", wan_trial_config(sample_enabled=False), [clip()], media_bytes=0)
    assert without_sample["samples"] == 0


def test_every_cached_window_and_image_adds_a_latent_but_only_items_add_text() -> None:
    config = wan_trial_config()
    one = estimate_disk("wan", config, [clip(1)], media_bytes=0)["caches"]
    four = estimate_disk("wan", config, [clip(4)], media_bytes=0)["caches"]
    assert four - one == 3 * 1_797_336
    image = estimate_disk("wan", config, [clip(1), {"kind": "image"}], media_bytes=0)["caches"]
    # One latent frame of 16 channels at 1/8 scale in two-byte values, plus the text cache and file header.
    assert image - one == pytest.approx(2 * 16 * 60 * 104 + 397_532, abs=300)


def test_invalid_draft_values_estimate_without_failing() -> None:
    config = wan_trial_config(save_every=0, steps=0, frames=-3, rank=-1, width=-16)
    estimate = estimate_disk("wan", config, [clip(None)], media_bytes=0)
    assert estimate["saves"] == 0
    assert estimate["checkpoints"] == 0


def roots(tmp_path: Path, output: str = "output", cache: str = "cache") -> Settings:
    return Settings(output_root=str(tmp_path / output), cache_root=str(tmp_path / cache))


def test_roots_on_one_volume_are_checked_against_their_combined_need(tmp_path: Path) -> None:
    estimate = {"output_root": 30 * GB, "cache_root": 10 * GB}
    [volume] = check_volumes(roots(tmp_path), estimate, free=lambda _: 100 * GB)
    assert volume["roots"] == ["Output root", "Cache root"]
    assert volume["needed"] == 40 * GB
    assert volume["margin"] == 4 * GB
    assert volume["free"] == 100 * GB
    assert volume["shortfall"] == 0
    assert volume["warning"] == ""


def test_a_short_volume_is_named_with_its_shortfall(tmp_path: Path) -> None:
    estimate = {"output_root": 30 * GB, "cache_root": 10 * GB}
    [volume] = check_volumes(roots(tmp_path), estimate, free=lambda _: 14 * GB)
    assert volume["shortfall"] == 30 * GB
    assert volume["volume"] in volume["warning"]
    assert "30.0 GB short" in volume["warning"]
    assert "Output root" in volume["warning"]


def test_small_runs_keep_a_minimum_margin(tmp_path: Path) -> None:
    estimate = {"output_root": 100, "cache_root": 100}
    [volume] = check_volumes(roots(tmp_path), estimate, free=lambda _: GB)
    assert volume["margin"] == GB
    assert volume["shortfall"] == 200


def test_unreadable_free_space_warns_without_claiming_a_shortfall(tmp_path: Path) -> None:
    def unavailable(_: Path) -> int:
        raise OSError("volume gone")

    [volume] = check_volumes(roots(tmp_path), {"output_root": GB, "cache_root": GB}, free=unavailable)
    assert volume["free"] is None
    assert volume["shortfall"] == 0
    assert "could not be read" in volume["warning"]


def test_unconfigured_roots_are_not_checked() -> None:
    assert check_volumes(Settings(), {"output_root": GB, "cache_root": GB}, free=lambda _: 0) == []

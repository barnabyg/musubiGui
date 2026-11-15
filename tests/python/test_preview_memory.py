import subprocess
import sys
from pathlib import Path

import pytest
from helpers import acceptance_workspace

from musubi_gui.jobs import Job, JobDraft, build_stages, smoke_config
from musubi_gui.preview_memory import GpuMemory, select_preview_memory
from musubi_gui.runs import RunService
from musubi_gui.settings import Settings


@pytest.mark.parametrize(("family", "fallback"), [("wan", 38), ("h3", 48)])
def test_unknown_capacity_uses_independent_preview_fallback(family: str, fallback: int) -> None:
    choice = select_preview_memory(family, (256, 256, 17), 12, native=False, representation="unknown", hardware=None)
    assert choice["blocks_to_swap"] == fallback
    assert choice["mode"] == "fallback"
    assert "unknown" in choice["reason"].lower()


@pytest.mark.parametrize("family", ["wan", "h3"])
def test_native_preview_preserves_the_training_offloader_and_explains_why(family: str) -> None:
    choice = select_preview_memory(
        family,
        (256, 256, 17),
        12,
        native=True,
        representation="unknown",
        hardware=GpuMemory("NVIDIA GeForce RTX 3090", 24 * 1024**3, 23 * 1024**3),
    )
    assert choice["blocks_to_swap"] == 12
    assert choice["mode"] == "training-residency"
    assert "training state is resident" in choice["reason"]


@pytest.mark.parametrize(("family", "fallback"), [("wan", 38), ("h3", 48)])
def test_preview_memory_is_recorded_without_changing_training_or_adapter_routing(
    tmp_path: Path, family: str, fallback: int
) -> None:
    job = JobDraft(
        name="Independent inference",
        family=family,
        config=smoke_config(family).model_copy(update={"blocks_to_swap": 12}),
    )
    persisted = Job(id="memory", revision=1, **job.model_dump())
    settings = Settings(
        models={
            key: str(tmp_path / key)
            for key in ["wan_low", "wan_high", "wan_vae", "wan_t5", "h3_dit", "h3_text", "h3_video_vae", "h3_audio_vae"]
        }
    )
    stages = build_stages(persisted, settings, tmp_path / "run", tmp_path / "cache", {"video"})
    training = next(stage.record() for stage in stages if stage.name == "train")
    generation = next(stage.record() for stage in stages if stage.name == "generate")
    assert training["args"][training["args"].index("--blocks_to_swap") + 1] == "12"
    assert generation["args"][generation["args"].index("--blocks_to_swap") + 1] == str(fallback)
    assert generation["preview_memory"]["blocks_to_swap"] == fallback
    assert "--lora_weight" in generation["args"]
    assert generation["args"][generation["args"].index("--video_length") + 1] == str(job.config.frames)


@pytest.mark.parametrize(
    ("failure", "expected_counts", "expected_code"),
    [
        ("capacity", [16, 36], 0),
        ("other", [16], 3),
        ("always-capacity", [16, 36], 3),
        ("capacity-after-output", [16], 3),
    ],
)
def test_generation_worker_recovers_capacity_once_after_the_failed_process_exits(
    tmp_path: Path, failure: str, expected_counts: list[int], expected_code: int
) -> None:
    import json

    worker = Path(__file__).resolve().parents[2] / "musubi_gui" / "preview_generate.py"
    upstream = tmp_path / "generator.py"
    attempts = tmp_path / "attempts.jsonl"
    output = tmp_path / "sample.mp4"
    upstream.write_text(
        "import json, pathlib, sys\n"
        "blocks = int(sys.argv[sys.argv.index('--blocks_to_swap') + 1])\n"
        f"with pathlib.Path({str(attempts)!r}).open('a') as stream: stream.write(json.dumps(blocks) + '\\n')\n"
        f"failure = {failure!r}\n"
        f"if failure == 'capacity-after-output': pathlib.Path({str(output)!r}).write_bytes(b'completed output')\n"
        "if failure == 'other': print('Invalid model format', flush=True); sys.exit(3)\n"
        "if blocks == 16 or failure == 'always-capacity': print('torch.OutOfMemoryError: CUDA out of memory.', flush=True); sys.exit(3)\n"
        "print('preview completed', flush=True)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(worker),
            "--fallback-blocks",
            "36",
            "--",
            str(upstream),
            "--blocks_to_swap",
            "16",
            "--save_path",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert [json.loads(line) for line in attempts.read_text().splitlines()] == expected_counts
    if failure == "capacity":
        assert "retrying once" in result.stdout.lower()
        assert "preview completed" in result.stdout
    if failure == "capacity-after-output":
        assert output.read_bytes() == b"completed output"


@pytest.mark.parametrize(
    ("family", "geometry", "representation", "blocks"),
    [
        ("wan", (256, 256, 17), "wan-a14b-fp16-runtime-scaled-fp8", 16),
        ("wan", (832, 480, 33), "wan-a14b-fp16-runtime-scaled-fp8", 18),
        ("h3", (256, 256, 124), "h3-pruned-convrot-int8", 32),
    ],
)
def test_qualified_profiles_reserve_headroom_and_choose_only_measured_counts(
    family: str, geometry: tuple[int, int, int], representation: str, blocks: int
) -> None:
    choice = select_preview_memory(
        family,
        geometry,
        0,
        native=False,
        representation=representation,
        hardware=GpuMemory("NVIDIA GeForce RTX 3090", 24 * 1024**3, 23 * 1024**3),
    )
    assert choice["mode"] == "automatic"
    assert choice["blocks_to_swap"] == blocks
    assert choice["estimated_peak_bytes"] + choice["reserve_bytes"] <= 24 * 1024**3


def test_one_byte_over_the_budget_keeps_an_extra_block_off_gpu() -> None:
    def select(free: int) -> dict:
        return select_preview_memory(
            "wan",
            (256, 256, 17),
            36,
            native=False,
            representation="wan-a14b-fp16-runtime-scaled-fp8",
            hardware=GpuMemory("NVIDIA GeForce RTX 3090", 24 * 1024**3, free),
        )

    boundary = int(20.5 * 1024**3)
    assert select(boundary)["blocks_to_swap"] == 16
    assert select(boundary - 1)["blocks_to_swap"] == 17


@pytest.mark.parametrize(
    ("name", "total", "free", "geometry"),
    [
        ("NVIDIA GeForce RTX 3090", 16, 15, (256, 256, 17)),
        ("NVIDIA GeForce RTX 3090", 48, 47, (256, 256, 17)),
        ("NVIDIA GeForce RTX 4090", 24, 23, (256, 256, 17)),
        ("NVIDIA GeForce RTX 3090", 24, 25, (256, 256, 17)),
        ("NVIDIA GeForce RTX 3090", 24, 4, (256, 256, 17)),
        ("NVIDIA GeForce RTX 3090", 24, 23, (512, 512, 81)),
    ],
)
def test_unqualified_or_busy_devices_never_enable_reduced_swapping(
    name: str, total: int, free: int, geometry: tuple[int, int, int]
) -> None:
    choice = select_preview_memory(
        "wan",
        geometry,
        0,
        native=False,
        representation="wan-a14b-fp16-runtime-scaled-fp8",
        hardware=GpuMemory(name, total * 1024**3, free * 1024**3),
    )
    assert choice["blocks_to_swap"] == 38
    assert choice["mode"] == "fallback"


def test_prepared_run_retains_its_memory_choice_across_service_reopening(tmp_path: Path) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    service = RunService(root, fixture_mode=True, autostart=False, device_path=tmp_path / "device.sqlite")
    try:
        original = service.prepare(fixture["jobs"][0]["id"])
        frozen = next(stage for stage in original["stages"] if stage["name"] == "generate")
        assert frozen["preview_memory"]["mode"] == "fallback"
        assert frozen["preview_memory"]["blocks_to_swap"] == 38
        service.cancel(original["id"])
        retried = service.retry(original["id"])
        assert (
            next(stage for stage in retried["stages"] if stage["name"] == "generate")["preview_memory"]
            == frozen["preview_memory"]
        )
    finally:
        service.close()
    reopened = RunService(root, fixture_mode=True, autostart=False, device_path=tmp_path / "device.sqlite")
    try:
        restored = reopened.get(original["id"])
        assert next(stage for stage in restored["stages"] if stage["name"] == "generate") == frozen
    finally:
        reopened.close()

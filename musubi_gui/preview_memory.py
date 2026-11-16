"""Freeze conservative inference residency independently from training settings."""

import shutil
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from musubi_gui.safetensors_file import safetensors_header
from musubi_gui.sampling import native_preview_frames, sample_geometry
from musubi_gui.settings import Settings

if TYPE_CHECKING:
    from musubi_gui.job_models import Job

GIB = 1024**3
MAX_BLOCKS = {"wan": 38, "h3": 48}
FALLBACK_BLOCKS = MAX_BLOCKS
BASELINE_BLOCKS = {"wan": 36, "h3": 48}
RESERVE_BYTES = 5 * GIB
MAX_QUALIFIED_RANK = {"wan": 8, "h3": 4}
MAX_QUALIFIED_SAMPLE_STEPS = 4
MAX_QUALIFIED_PROMPT_CHARACTERS = 256


@dataclass(frozen=True)
class GpuMemory:
    name: str
    total_bytes: int
    free_bytes: int


@dataclass(frozen=True)
class QualifiedPreview:
    family: str
    geometry: tuple[int, int, int]
    representation: str
    minimum_swapped_blocks: int
    baseline_peak_bytes: int
    additional_resident_block_bytes: int


# Measured on the pinned checkout and RTX 3090; see preview-memory.json.
# Baseline floors exceed sampled peaks. Per-block increments exceed represented
# block weights and cover additional residency; the separate 5 GiB reserve is
# retained for unsampled transients, larger text, decoding, allocator overhead and
# adapter allocations. Geometry is exact: there is no unmeasured extrapolation.
QUALIFIED_PREVIEWS = (
    QualifiedPreview("wan", (256, 256, 17), "wan-a14b-fp16-runtime-scaled-fp8", 16, 9 * GIB, 384 * 1024**2),
    QualifiedPreview("wan", (832, 480, 33), "wan-a14b-fp16-runtime-scaled-fp8", 18, 12 * GIB, 384 * 1024**2),
    QualifiedPreview("h3", (256, 256, 124), "h3-pruned-convrot-int8", 32, 9 * GIB, 512 * 1024**2),
)


def gpu_memory() -> GpuMemory | None:
    """A missing, ambiguous or invalid device report never enables reduced swapping."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        # B603: fixed read-only query; multiple GPUs are deliberately outside qualification.
        result = subprocess.run(
            [smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )  # nosec B603
        lines = result.stdout.strip().splitlines()
        if result.returncode or len(lines) != 1:
            return None
        name, total, free = [part.strip() for part in lines[0].split(",")]
        hardware = GpuMemory(name, int(total) * 1024**2, int(free) * 1024**2)
        return hardware if 0 <= hardware.free_bytes <= hardware.total_bytes else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def select_preview_memory(
    family: str,
    geometry: tuple[int, int, int],
    training_blocks: int,
    *,
    native: bool,
    representation: str,
    hardware: GpuMemory | None,
    policy: str = "automatic",
) -> dict[str, Any]:
    """Select only qualified inference configurations; retain unsupported native residency."""
    record: dict[str, Any] = {
        "version": 1,
        "family": family,
        "geometry": {"width": geometry[0], "height": geometry[1], "frames": geometry[2]},
        "representation": representation,
        "training_blocks_to_swap": training_blocks,
        "reserve_bytes": RESERVE_BYTES,
        "blocks_to_swap": FALLBACK_BLOCKS[family],
        "supported_swap_range": [0, MAX_BLOCKS[family]],
        "qualification_limits": {
            "max_rank": MAX_QUALIFIED_RANK[family],
            "max_sample_steps": MAX_QUALIFIED_SAMPLE_STEPS,
            "max_prompt_characters": MAX_QUALIFIED_PROMPT_CHARACTERS,
        },
        "mode": "fallback",
        "reason": "GPU capacity or model representation is unknown or outside the qualified preview range; using the conservative family preset.",
    }
    if native:
        record.update(
            blocks_to_swap=training_blocks,
            mode="training-residency",
            reason="In-run previews keep training memory settings; the current trainer cannot safely change them while training state is resident.",
            limitation="The pinned trainer has no public offloader teardown/reconfiguration interface. Its existing inference/training mode switches preserve the optimizer, CPU masters and training offloader.",
        )
        return record
    if hardware:
        record["hardware"] = {
            "name": hardware.name,
            "total_bytes": hardware.total_bytes,
            "free_bytes": hardware.free_bytes,
        }
    if policy == "conservative":
        record["reason"] = (
            "Conservative family preset selected for standalone previews and final samples; training settings stay independent."
        )
        return record
    profile = next(
        (
            profile
            for profile in QUALIFIED_PREVIEWS
            if (profile.family, profile.geometry, profile.representation) == (family, geometry, representation)
        ),
        None,
    )
    if (
        profile is None
        or hardware is None
        or hardware.name not in {"NVIDIA GeForce RTX 3090", "NVIDIA RTX 3090"}
        or hardware.total_bytes != 24 * GIB
        or not 0 <= hardware.free_bytes <= hardware.total_bytes
    ):
        return record
    # Reference peaks already included up to 1 GiB of desktop/other GPU use.
    other_consumers = max(0, hardware.total_bytes - hardware.free_bytes - GIB)
    for swapped in range(profile.minimum_swapped_blocks, BASELINE_BLOCKS[family]):
        peak = (
            profile.baseline_peak_bytes
            + other_consumers
            + (BASELINE_BLOCKS[family] - swapped) * profile.additional_resident_block_bytes
        )
        if peak + RESERVE_BYTES <= hardware.total_bytes:
            record.update(
                mode="automatic",
                blocks_to_swap=swapped,
                estimated_peak_bytes=peak,
                qualification="preview-memory-20261115",
                recovery={
                    "attempts": 1,
                    "blocks_to_swap": FALLBACK_BLOCKS[family],
                    "trigger": "CUDA memory-capacity failure",
                },
                reason="A measured profile matches this model, output size and RTX 3090. The budget retains at least 5 GiB for transient allocations, text/decoding, allocator overhead and other GPU consumers. Training memory settings are unchanged.",
            )
            return record
    record["reason"] = (
        "The measured profile cannot retain its 5 GiB reserve with current GPU use; using the conservative family preset."
    )
    return record


def _representation(job: "Job", settings: Settings) -> str:
    try:
        if job.family == "wan" and job.config.fp8_scaled:
            for key in ["wan_low", "wan_high"]:
                header = safetensors_header(Path(settings.models.get(key, "")))
                tensor = header.get("blocks.0.cross_attn.k.weight", {})
                if tensor.get("dtype") != "F16" or tensor.get("shape") != [5120, 5120]:
                    return "unknown"
                if "blocks.39.cross_attn.k.weight" not in header:
                    return "unknown"
                if Path(settings.models[key]).stat().st_size > 27 * GIB:
                    return "unknown"
            if (
                Path(settings.models["wan_t5"]).stat().st_size > 12 * GIB
                or Path(settings.models["wan_vae"]).stat().st_size > GIB
            ):
                return "unknown"
            return "wan-a14b-fp16-runtime-scaled-fp8"
        if job.family == "h3":
            header = safetensors_header(Path(settings.models.get("h3_dit", "")))
            tensor = header.get("blocks.0.attn.qkv_proj.weight", {})
            if (
                "blocks.49.attn.qkv_proj.weight_scale" in header
                and tensor.get("dtype") == "I8"
                and tensor.get("shape") == [21504, 5376]
                and header.get("blocks.0.adaln_proj.linear.weight", {}).get("shape") == [96768, 8]
                and Path(settings.models["h3_dit"]).stat().st_size <= 21 * GIB
                and Path(settings.models["h3_text"]).stat().st_size <= 16 * GIB
                and Path(settings.models["h3_video_vae"]).stat().st_size <= 5 * GIB
                and Path(settings.models["h3_audio_vae"]).stat().st_size <= GIB
            ):
                return "h3-pruned-convrot-int8"
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        pass
    return "unknown"


def resolve_preview_memory(job: "Job", settings: Settings, *, native: bool) -> dict[str, Any]:
    width, height, frames = sample_geometry(job.config)
    if native:
        frames = native_preview_frames(job.family, job.config)
    representation = _representation(job, settings)
    if (
        len(job.config.sample_prompt) > MAX_QUALIFIED_PROMPT_CHARACTERS
        or job.config.rank > MAX_QUALIFIED_RANK[job.family]
        or job.config.sample_steps > MAX_QUALIFIED_SAMPLE_STEPS
    ):
        representation = "unknown"
    choice = select_preview_memory(
        job.family,
        (width, height, frames),
        job.config.blocks_to_swap,
        native=native,
        representation=representation,
        hardware=None if native or representation == "unknown" else gpu_memory(),
        policy=job.config.sample_memory_policy,
    )
    choice.update(
        adapter_rank=job.config.rank,
        sample_steps=job.config.sample_steps,
        prompt_characters=len(job.config.sample_prompt),
    )
    return choice

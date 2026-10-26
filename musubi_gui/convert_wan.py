"""Stream legacy Comfy scaled-FP8 Wan weights into separate native FP16 files.

This reconstructs the quantized values; it cannot recover precision lost to FP8.
The application Python is sufficient; NumPy accelerates BF16 tensors when present.
"""

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from musubi_gui.reference import safetensors_header
from musubi_gui.settings import atomic_json

CHUNK = 32 * 1024**2
SIZES = {"F8_E4M3": 1, "F16": 2, "BF16": 2, "F32": 4}


def native_key(key: str, family: str) -> str | None:
    if key in {"__metadata__", "scaled_fp8", "spiece_model"} or key.endswith((".scale_weight", ".scale_input")):
        return None
    if family == "dit":
        return key
    if key == "shared.weight":
        return "token_embedding.weight"
    if key == "encoder.final_layer_norm.weight":
        return "norm.weight"
    match = re.fullmatch(r"encoder\.block\.(\d+)\.layer\.(\d+)\.(.+)", key)
    if not match:
        raise ValueError(f"Unknown T5 source key: {key}; conversion refuses to discard unsupported data.")
    block, layer, suffix = match.groups()
    mapping = {
        ("0", "layer_norm.weight"): "norm1.weight",
        ("1", "layer_norm.weight"): "norm2.weight",
        ("0", "SelfAttention.relative_attention_bias.weight"): "pos_embedding.embedding.weight",
        **{("0", f"SelfAttention.{name}.weight"): f"attn.{name}.weight" for name in ["q", "k", "v", "o"]},
        ("1", "DenseReluDense.wi_0.weight"): "ffn.gate.0.weight",
        ("1", "DenseReluDense.wi_1.weight"): "ffn.fc1.weight",
        ("1", "DenseReluDense.wo.weight"): "ffn.fc2.weight",
    }
    mapped = mapping.get((layer, suffix))
    if mapped is None:
        raise ValueError(f"Unknown T5 source key: {key}; inspect the source architecture before conversion.")
    return f"blocks.{block}.{mapped}"


def fp8_tables(scale: float) -> tuple[bytes, bytes]:
    low, high = bytearray(), bytearray()
    for bits in range(256):
        exponent, fraction = (bits >> 3) & 15, bits & 7
        if exponent == 15 and fraction == 7:
            value = math.nan
        elif exponent == 0:
            value = fraction * 2.0**-9
        else:
            value = (1.0 + fraction / 8.0) * 2.0 ** (exponent - 7)
        if bits & 128:
            value = -value
        packed = struct.pack("<e", value * scale)
        low.append(packed[0])
        high.append(packed[1])
    return bytes(low), bytes(high)


def fp16(raw: bytes, dtype: str) -> bytes:
    if dtype == "F16":
        return raw
    try:
        numpy: Any = importlib.import_module("numpy")
    except ImportError:
        if dtype == "BF16":
            values = (
                struct.unpack("<f", struct.pack("<I", bits[0] << 16))[0] for bits in struct.iter_unpack("<H", raw)
            )
        else:
            values = (value[0] for value in struct.iter_unpack("<f", raw))
        return b"".join(struct.pack("<e", value) for value in values)
    if dtype == "BF16":
        data = (numpy.frombuffer(raw, dtype="<u2").astype("<u4") << 16).view("<f4")
    else:
        data = numpy.frombuffer(raw, dtype="<f4")
    if not numpy.isfinite(data).all() or numpy.abs(data).max(initial=0) > 65504:
        raise ValueError("A source tensor is nonfinite or exceeds FP16 range; no silent clipping is allowed.")
    return bytes(data.astype("<f2").tobytes())


def convert_tensor(source: BinaryIO, output: BinaryIO, entry: dict[str, Any], base: int, scale: float | None) -> None:
    start, end = entry["data_offsets"]
    source.seek(base + start)
    remaining = end - start
    tables = fp8_tables(scale) if scale is not None else None
    while remaining:
        raw = source.read(min(CHUNK, remaining))
        if not raw:
            raise ValueError("Source is incomplete; no completed output will be published.")
        remaining -= len(raw)
        if tables is not None:
            if 127 in raw or 255 in raw:
                raise ValueError("FP8 source contains NaN values; conversion refuses nonfinite weights.")
            expanded = bytearray(len(raw) * 2)
            expanded[0::2] = raw.translate(tables[0])
            expanded[1::2] = raw.translate(tables[1])
            output.write(expanded)
        else:
            output.write(fp16(raw, entry["dtype"]))


def convert(source: Path, destination: Path, family: str) -> dict[str, Any]:
    source, destination = source.resolve(), destination.resolve()
    provenance_file = destination.with_suffix(".provenance.json")
    if source == destination or destination.exists() or provenance_file.exists():
        raise ValueError("Choose a new destination; source weights and existing conversions are never overwritten.")
    before = source.stat()
    header = safetensors_header(source)
    with source.open("rb") as stream:
        base = 8 + int.from_bytes(stream.read(8), "little")
    result: dict[str, Any] = {
        "__metadata__": {
            "conversion": "legacy-Comfy-scaled-FP8-to-native-FP16-v1",
            "source_precision": "LOSSY FP8; dequantization does not recover original full precision",
        }
    }
    entries: list[tuple[str, dict[str, Any], float | None]] = []
    offset = 0
    with source.open("rb") as stream:
        for key, entry in header.items():
            mapped = native_key(key, family)
            if mapped is None:
                continue
            dtype = entry.get("dtype")
            shape, bounds = entry.get("shape"), entry.get("data_offsets")
            if (
                dtype not in SIZES
                or not isinstance(shape, list)
                or not all(isinstance(d, int) and d >= 0 for d in shape)
                or not isinstance(bounds, list)
                or len(bounds) != 2
                or not all(isinstance(bound, int) and bound >= 0 for bound in bounds)
            ):
                raise ValueError(f"Unsupported or malformed tensor: {key}.")
            count = math.prod(shape)
            if bounds[1] - bounds[0] != count * SIZES[dtype] or base + bounds[1] > before.st_size:
                raise ValueError(f"Tensor size/bounds mismatch or incomplete source: {key}.")
            if mapped in result:
                raise ValueError(f"Duplicate native key: {mapped}.")
            scale: float | None = None
            if dtype == "F8_E4M3":
                scale_entry = header.get(key.removesuffix(".weight") + ".scale_weight")
                if not scale_entry or scale_entry.get("dtype") != "F32" or scale_entry.get("shape") not in [[], [1]]:
                    raise ValueError(f"FP8 weight requires a scalar F32 weight scale: {key}.")
                stream.seek(base + scale_entry["data_offsets"][0])
                scale = struct.unpack("<f", stream.read(4))[0]
                if not math.isfinite(scale) or scale <= 0 or scale * 448 > 65504:
                    raise ValueError(f"Invalid or out-of-range FP8 scale: {key}.")
            result[mapped] = {"dtype": "F16", "shape": shape, "data_offsets": [offset, offset + count * 2]}
            offset += count * 2
            entries.append((mapped, entry, scale))
    if not entries:
        raise ValueError("No supported model tensors found.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < offset + 1024**3:
        raise ValueError(f"Need {offset + 1024**3} free bytes for the new conversion plus a 1 GiB safety margin.")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 8)
    descriptor, pending = tempfile.mkstemp(dir=destination.parent, prefix=".pending-wan-", suffix=".safetensors")
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            output.write(len(encoded).to_bytes(8, "little"))
            output.write(encoded)
            for index, (mapped, entry, scale) in enumerate(entries, 1):
                convert_tensor(input_stream, output, entry, base, scale)
                if index % 40 == 0:
                    print(f"Converted {index}/{len(entries)} tensors ({mapped})", flush=True)
            output.flush()
            os.fsync(output.fileno())
        after = source.stat()
        if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise ValueError("Source changed during conversion; completed output was not published.")
        if os.name == "nt":
            os.rename(pending, destination)  # Windows rename refuses a destination created concurrently.
        else:
            os.link(pending, destination)  # POSIX link publishes exclusively without replacing existing files.
    finally:
        Path(pending).unlink(missing_ok=True)
    provenance: dict[str, Any] = {
        "version": 1,
        "family": family,
        "source": str(source),
        "source_bytes": before.st_size,
        "source_mtime_ns": before.st_mtime_ns,
        "source_header_sha256": hashlib.sha256(json.dumps(header, sort_keys=True).encode()).hexdigest(),
        "destination": str(destination),
        "destination_bytes": destination.stat().st_size,
        "destination_header_sha256": hashlib.sha256(encoded).hexdigest(),
        "converted_tensors": len(entries),
        "lossy_source": True,
        "activation_scales_applied": False,
        "support_gate": "unresolved",
        "created_utc": datetime.now(UTC).isoformat(),
        "method": "weight_fp16 = float(weight_fp8) * scalar_scale_weight; activation scale metadata omitted",
    }
    atomic_json(provenance_file, provenance)
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=["dit", "t5"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        provenance = convert(args.source, args.destination, args.family)
    except (ValueError, OSError, OverflowError, struct.error) as error:
        print(str(error))
        raise SystemExit(2) from error
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()

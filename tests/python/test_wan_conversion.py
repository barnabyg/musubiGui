import json
import struct
import subprocess
import sys
from pathlib import Path


def test_offline_wan_conversion_applies_weight_scale_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "scaled.safetensors"
    header = {
        "blocks.0.cross_attn.k.scale_weight": {"dtype": "F32", "shape": [], "data_offsets": [0, 4]},
        "blocks.0.cross_attn.k.scale_input": {"dtype": "F32", "shape": [], "data_offsets": [4, 8]},
        "blocks.0.cross_attn.k.weight": {"dtype": "F8_E4M3", "shape": [1, 5], "data_offsets": [8, 13]},
    }
    encoded = json.dumps(header).encode()
    original = (
        len(encoded).to_bytes(8, "little") + encoded + struct.pack("<ff", 0.25, 7.0) + bytes([0, 56, 64, 184, 126])
    )
    source.write_bytes(original)
    destination = tmp_path / "native.safetensors"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musubi_gui.convert_wan",
            "dit",
            "--source",
            str(source),
            "--destination",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() == original
    with destination.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        converted = json.loads(stream.read(size))
        assert converted["blocks.0.cross_attn.k.weight"]["dtype"] == "F16"
        assert not any("scale_" in key for key in converted)
        assert struct.unpack("<5e", stream.read()) == (0.0, 0.25, 0.5, -0.25, 112.0)
    provenance = json.loads(destination.with_suffix(".provenance.json").read_text(encoding="utf-8"))
    assert provenance["lossy_source"] is True
    assert provenance["support_gate"] == "unresolved"
    assert provenance["activation_scales_applied"] is False


def test_t5_conversion_maps_transformers_encoder_keys_to_native_wan_architecture(tmp_path: Path) -> None:
    source = tmp_path / "t5-scaled.safetensors"
    keys = [
        "shared.weight",
        "encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight",
        "encoder.block.0.layer.0.layer_norm.weight",
        "encoder.block.0.layer.0.SelfAttention.q.weight",
        "encoder.block.0.layer.1.DenseReluDense.wi_0.weight",
        "encoder.block.0.layer.1.DenseReluDense.wi_1.weight",
        "encoder.block.0.layer.1.DenseReluDense.wo.weight",
        "encoder.block.0.layer.1.layer_norm.weight",
        "encoder.final_layer_norm.weight",
    ]
    header = {
        key: {"dtype": "F32", "shape": [1], "data_offsets": [index * 4, (index + 1) * 4]}
        for index, key in enumerate(keys)
    }
    encoded = json.dumps(header).encode()
    source.write_bytes(len(encoded).to_bytes(8, "little") + encoded + struct.pack("<9f", *range(1, 10)))
    destination = tmp_path / "native.safetensors"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "musubi_gui.convert_wan",
            "t5",
            "--source",
            str(source),
            "--destination",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with destination.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        converted = json.loads(stream.read(size))
        assert set(converted) - {"__metadata__"} == {
            "token_embedding.weight",
            "blocks.0.pos_embedding.embedding.weight",
            "blocks.0.norm1.weight",
            "blocks.0.attn.q.weight",
            "blocks.0.ffn.gate.0.weight",
            "blocks.0.ffn.fc1.weight",
            "blocks.0.ffn.fc2.weight",
            "blocks.0.norm2.weight",
            "norm.weight",
        }
        assert struct.unpack("<9e", stream.read()) == tuple(range(1, 10))


def test_conversion_preserves_existing_destinations_and_rejects_unknown_t5_data(tmp_path: Path) -> None:
    source, destination = tmp_path / "source.safetensors", tmp_path / "existing.safetensors"
    encoded = json.dumps({"new.unsupported.weight": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}).encode()
    source.write_bytes(len(encoded).to_bytes(8, "little") + encoded + struct.pack("<e", 1.0))
    destination.write_bytes(b"existing model")
    command = [sys.executable, "-m", "musubi_gui.convert_wan", "t5", "--source", str(source), "--destination"]
    result = subprocess.run([*command, str(destination)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "never overwritten" in result.stdout
    assert destination.read_bytes() == b"existing model"
    unknown = tmp_path / "new.safetensors"
    result = subprocess.run([*command, str(unknown)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "Unknown T5 source key" in result.stdout
    assert not unknown.exists()

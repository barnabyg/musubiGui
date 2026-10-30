import json
import subprocess
import sys
from pathlib import Path


def test_h3_nvfp4_block_scales_do_not_get_rejected_as_fp8_weights(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    paths = {}
    for name in ["h3_dit", "h3_video_vae", "h3_audio_vae", "h3_text"]:
        file = models / f"{name}.safetensors"
        header = {"weight": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}
        if name == "h3_text":
            header = {
                "model.layers.0.mlp.down_proj.weight_scale": {"dtype": "F8_E4M3", "shape": [1], "data_offsets": [0, 1]}
            }
        data = json.dumps(header).encode()
        file.write_bytes(len(data).to_bytes(8, "little") + data + b"\x00\x00")
        paths[name] = str(file)
    state = tmp_path / "settings.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "checkout": str(tmp_path / "upstream"),
                "python": sys.executable,
                "output_root": str(tmp_path / "outputs"),
                "cache_root": str(tmp_path / "cache"),
                "model_roots": [str(models)],
                "models": paths,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "musubi_gui.reference", "h3", "--settings", str(state), "--plan-only"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 2
    evidence = json.loads(next((tmp_path / "outputs").glob("h3-*/evidence.json")).read_text(encoding="utf-8"))
    assert not any("does not support FP8" in blocker for blocker in evidence["blockers"])
    assert evidence["support_gate"] == "unresolved"

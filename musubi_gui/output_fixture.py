"""Original CC0 synthetic output fixtures. These files are not GPU training evidence."""

import argparse
import io
import json
import math
import struct
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import av
from PIL import Image

from musubi_gui.jobs import Job, default_config
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings, atomic_json, save_settings


def sample_files(directory: Path, *, audio: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (256, 256), "steelblue").save(directory / "still.png")
    with av.open(str(directory / "sample.mp4"), "w") as container:
        video: Any = container.add_stream("libx264", rate=24)
        video.width, video.height, video.pix_fmt = 256, 256, "yuv420p"
        audio_stream: Any = container.add_stream("aac", rate=24000) if audio else None
        if audio_stream is not None:
            audio_stream.layout = "mono"
        for index in range(24):
            frame = av.VideoFrame(256, 256, "yuv420p")
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes([48 + index if plane_index == 0 else 128]) * plane.buffer_size)
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if audio:
            stream = audio_stream
            for start in range(0, 24000, 1024):
                count = min(1024, 24000 - start)
                sound = av.AudioFrame(format="fltp", layout="mono", samples=count)
                sound.sample_rate, sound.pts, sound.time_base = 24000, start, Fraction(1, 24000)
                sound.planes[0].update(
                    b"".join(
                        struct.pack("<f", 0.1 * math.sin(2 * math.pi * 440 * (start + offset) / 24000))
                        for offset in range(count)
                    )
                )
                for packet in stream.encode(sound):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    if audio:
        with av.open(str(directory / "tone.wav"), "w") as container:
            stream = container.add_stream("pcm_s16le", rate=24000)
            stream.layout = "mono"
            sound = av.AudioFrame(format="s16", layout="mono", samples=24000)
            sound.sample_rate = 24000
            sound.planes[0].update(
                b"".join(
                    struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * offset / 24000)))
                    for offset in range(24000)
                )
            )
            for packet in stream.encode(sound):
                container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)


def prepare(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    settings = Settings(output_root=str(root / "output"), cache_root=str(root / "cache"))
    state = root / "state"
    save_settings(state, settings)
    service = RunService(state, device_path=root / "fixture-device.sqlite3", fixture_mode=True, autostart=False)
    records = []
    families: list[Literal["wan", "h3"]] = ["wan", "h3"]
    for family in families:
        for revision in [1, 2]:
            token = f"{family}-revision-{revision}"
            config = default_config(family).model_copy(update={"rank": 4 * revision})
            job = Job(id=family + "-fixture", name=token, family=family, revision=revision, config=config)
            run = service.submit(
                ExecutionPlan(
                    job=job.model_dump(),
                    settings=settings,
                    manifest={
                        "items": [
                            {
                                "id": "fixture-item",
                                "path": "source.mp4",
                                "sha256": f"fixture-revision-{revision}",
                                "caption": {"text": f"A blue field with fixture caption revision {revision}."},
                            }
                        ]
                    },
                    directory=root / "output" / token,
                    cache=root / "cache" / token,
                    stages=[],
                    fingerprint=token,
                    provenance={
                        "synthetic_fixture": True,
                        "upstream_revision": f"synthetic-revision-{revision}",
                        "models": {family: {"identity": f"synthetic-{revision}"}},
                    },
                )
            )
            directory = Path(run["directory"])
            checkpoints = directory / "checkpoints"
            checkpoints.mkdir()
            header = json.dumps(
                {
                    "__metadata__": {"fixture": "synthetic; not trained", "ss_steps": str(revision)},
                    "fixture.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
                }
            ).encode()
            header += b" " * (-len(header) % 8)
            (checkpoints / "fixture.safetensors").write_bytes(
                len(header).to_bytes(8, "little") + header + struct.pack("<f", float(revision))
            )
            sample_files(directory / "samples", audio=family == "h3")
            service.store.update(run["id"], lambda record: record.update(status="completed", phase="fixture"))
            records.append({"id": run["id"], "family": family, "revision": revision, "directory": str(directory)})
    result = {"version": 1, "kind": "synthetic-output-fixture", "state": str(state), "runs": records}
    atomic_json(root / "output-fixture.json", result)
    return result


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="A new isolated directory; existing directories are preserved.")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    root = args.root.resolve()
    result = prepare(root)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.launch:
        import uvicorn

        from musubi_gui.app import create_app

        uvicorn.run(
            create_app(root / "state", fixture_mode=True, device_path=root / "fixture-device.sqlite3"),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
        )


if __name__ == "__main__":
    main()

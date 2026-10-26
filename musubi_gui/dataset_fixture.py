"""Original CC0 media and isolated dataset acceptance workspace; no weights or training required."""

import argparse
import io
import sys
from pathlib import Path
from typing import Any

import av
from PIL import Image

from musubi_gui.settings import Settings, save_settings


def prepare(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    media = root / "media 资料"
    media.mkdir()
    for name, colour in [("blue.png", "blue"), ("red.png", "red")]:
        Image.new("RGB", (256, 256), colour).save(media / name)
    (media / "red.txt").write_text("A red square.\n红色方块", encoding="utf-8")
    (media / "invalid.mp4").write_bytes(b"This is intentionally invalid fixture media.")
    with av.open(str(media / "motion.mp4"), "w") as container:
        stream: Any = container.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = 256, 256, "yuv420p"
        for index in range(124):
            frame = av.VideoFrame(256, 256, "yuv420p")
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes([48 + index // 4 if plane_index == 0 else 128]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    (media / "motion.txt").write_text("A blue field gradually brightens.", encoding="utf-8")
    save_settings(root / "state", Settings(dataset_roots=[str(media.resolve())]))
    return media


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="A new isolated fixture directory; existing directories are preserved.")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    media = prepare(args.root.resolve())
    print(f"Register this folder in Datasets: {media}", flush=True)
    if args.launch:
        import uvicorn

        from musubi_gui.app import create_app

        uvicorn.run(create_app(args.root.resolve() / "state"), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

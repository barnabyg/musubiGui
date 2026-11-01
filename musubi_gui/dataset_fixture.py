"""Original CC0 media and isolated dataset acceptance workspace; no weights or training required."""

import argparse
import io
import math
import shutil
import struct
import sys
import wave
from fractions import Fraction
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


def add_separate_folders(root: Path, media: Path) -> tuple[Path, Path]:
    """A folder of images and a folder of videos, the usual way to combine media in one job."""
    images, videos = root / "images", root / "videos"
    images.mkdir()
    videos.mkdir()
    for name, colour in [("green.png", "green"), ("yellow.png", "yellow")]:
        Image.new("RGB", (256, 256), colour).save(images / name)
        (images / name).with_suffix(".txt").write_text(f"A {colour} square.", encoding="utf-8")
    shutil.copyfile(media / "motion.mp4", videos / "motion.mp4")
    (videos / "motion.txt").write_text("A blue field gradually brightens.", encoding="utf-8")
    write_tone(videos / "motion.wav", 124 / 24)
    roots = [str(media.resolve()), str(images.resolve()), str(videos.resolve())]
    save_settings(root / "state", Settings(dataset_roots=roots))
    return images, videos


def write_tone(file: Path, seconds: float) -> None:
    with wave.open(str(file), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(32000)
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * index / 32000)))
                for index in range(round(seconds * 32000))
            )
        )


def add_h3_cases(media: Path) -> None:
    for name in ["missing", "sidecar", "ambiguous", "malformed", "short"]:
        shutil.copyfile(media / "motion.mp4", media / f"{name}.mp4")
    for name, seconds in [
        ("sidecar.wav", 124 / 24),
        ("chosen.wav", 124 / 24),
        ("ambiguous.wav", 124 / 24),
        ("short.wav", 0.5),
    ]:
        write_tone(media / name, seconds)
    (media / "ambiguous.flac").write_bytes(b"Ambiguous sidecar fixture; not selected for decoding.")
    (media / "malformed.wav").write_bytes(b"Not audio.")
    for name, offset in [("embedded", 0), ("late", 12800)]:
        with av.open(str(media / f"{name}.mp4"), "w") as container:
            video: Any = container.add_stream("libx264", rate=24)
            video.width, video.height, video.pix_fmt = 256, 256, "yuv420p"
            audio_stream: Any = container.add_stream("aac", rate=32000)
            audio_stream.layout = "mono"
            for index in range(124):
                frame = av.VideoFrame(256, 256, "yuv420p")
                for plane in frame.planes:
                    plane.update(bytes([128]) * plane.buffer_size)
                for packet in video.encode(frame):
                    container.mux(packet)
            for packet in video.encode():
                container.mux(packet)
            for index in range(162):
                sound = av.AudioFrame(format="s16", layout="mono", samples=1024)
                sound.sample_rate, sound.pts, sound.time_base = 32000, index * 1024 + offset, Fraction(1, 32000)
                sound.planes[0].update(b"\0" * sound.planes[0].buffer_size)
                for packet in audio_stream.encode(sound):
                    container.mux(packet)
            for packet in audio_stream.encode():
                container.mux(packet)


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="A new isolated fixture directory; existing directories are preserved.")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--h3-audio", action="store_true")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    media = prepare(args.root.resolve())
    if args.h3_audio:
        add_h3_cases(media)
    images, videos = add_separate_folders(args.root.resolve(), media)
    print(f"Register this folder in Datasets: {media}", flush=True)
    print(f"To combine media in one job, also register the image folder: {images}", flush=True)
    print(f"and the video folder: {videos}", flush=True)
    if args.launch:
        import uvicorn

        from musubi_gui.app import create_app

        uvicorn.run(create_app(args.root.resolve() / "state"), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

"""CC0 synthetic reference media; run only through the selected training Python."""

import argparse
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any


def prepare(root: Path, family: str) -> None:
    import av
    import numpy as np  # type: ignore[import-not-found]

    root.mkdir(parents=True, exist_ok=True)
    frames, fps = (17, 16) if family == "wan" else (124, 24)
    container: Any = av.open(str(root / "cube.mp4"), "w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height, stream.pix_fmt = 256, 256, "yuv420p"
    for index in range(frames):
        image = np.zeros((256, 256, 3), dtype=np.uint8)
        image[:, :] = [24, 62, 125]
        x = int(160 * index / (frames - 1))
        image[96:160, x : x + 64] = [220, 52, 60]
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    caption = "A red square moves across a blue background."
    if family == "h3":
        caption += " A steady sine tone plays."
        with wave.open(str(root / "cube.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(
                b"".join(
                    struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * t / 48000)))
                    for t in range(round(frames * 48000 / fps))
                )
            )
    (root / "cube.txt").write_text(caption, encoding="utf-8")


def inspect(path: Path) -> dict[str, int]:
    import av

    with av.open(str(path)) as media:
        frames = sum(1 for _ in media.decode(video=0))
    with av.open(str(path)) as media:
        samples = sum(frame.samples for frame in media.decode(audio=0)) if media.streams.audio else 0
    return {"video_frames": frames, "audio_samples": samples}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("family", choices=["wan", "h3", "inspect"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.family == "inspect":
        print("MUSUBI_MEDIA=" + json.dumps(inspect(args.path)))
    else:
        prepare(args.path, args.family)


if __name__ == "__main__":
    main()

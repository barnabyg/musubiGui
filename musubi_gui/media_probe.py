"""Read media through the selected training Python; no training imports or writes to source media."""

import argparse
import json
from pathlib import Path
from typing import Any


def inspect(path: Path, thumbnail: Path | None = None) -> dict[str, Any]:
    import av
    from PIL import Image

    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if thumbnail:
                image.thumbnail((256, 192))
                image.convert("RGB").save(thumbnail, "JPEG")
        return {"width": width, "height": height, "frames": 1, "duration": 0, "audio": None}
    result: dict[str, Any] = {"audio": None}
    with av.open(str(path)) as media:
        if media.streams.video:
            video = media.streams.video[0]
            first: Any = next(media.decode(video=0), None)
            if first is None:
                raise ValueError("No decodable video frames.")
            if thumbnail:
                image = first.to_image()
                image.thumbnail((256, 192))
                image.convert("RGB").save(thumbnail, "JPEG")
            result.update(
                width=video.width,
                height=video.height,
                frames=video.frames,
                fps=float(video.average_rate or 0),
                duration=float(video.duration * video.time_base)
                if video.duration is not None and video.time_base
                else float(media.duration or 0) / 1e6,
                video_start=float(first.time or 0),
            )
        elif path.suffix.lower() not in {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}:
            raise ValueError("No video stream found.")
    with av.open(str(path)) as media:
        if media.streams.audio:
            audio = media.streams.audio[0]
            first_audio = next(media.decode(audio=0), None)
            if first_audio is None:
                raise ValueError("Audio stream has no decodable samples.")
            result["audio"] = {
                "sample_rate": audio.rate,
                "channels": audio.channels,
                "duration": float(audio.duration * audio.time_base)
                if audio.duration is not None and audio.time_base
                else float(media.duration or 0) / 1e6,
                "start": float(first_audio.time or 0),
                "codec": audio.codec_context.name,
            }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--thumbnail", type=Path)
    args = parser.parse_args()
    try:
        result = {"valid": True, "metadata": inspect(args.path, args.thumbnail)}
    except Exception as error:
        result = {
            "valid": False,
            "error": f"Media cannot be decoded: {type(error).__name__}. Re-encode or exclude this item.",
        }
    print("MUSUBI_MEDIA=" + json.dumps(result))


if __name__ == "__main__":
    main()

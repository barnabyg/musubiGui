"""Read media through the selected training Python; no training imports or writes to source media."""

import argparse
import json
from pathlib import Path
from typing import Any


def inspect(
    path: Path, thumbnail: Path | None = None, timeline: bool = False, extension: str | None = None
) -> dict[str, Any]:
    import av
    from PIL import Image

    suffix = (extension or path.suffix).lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if thumbnail:
                image.thumbnail((256, 192))
                image.convert("RGB").save(thumbnail, "JPEG")
            image_format = image.format
        return {"width": width, "height": height, "frames": 1, "duration": 0, "audio": None, "format": image_format}
    result: dict[str, Any] = {"audio": None}
    with av.open(str(path)) as media:
        result["format"] = media.format.name
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
            if timeline:
                count = 1
                previous = float(first.time or 0)
                backwards = 0.0
                for decoded in media.decode(video=0):
                    timestamp = (
                        float(decoded.time) if decoded.time is not None else previous + 1 / max(result["fps"], 1)
                    )
                    backwards = max(backwards, previous - timestamp)
                    previous = timestamp
                    count += 1
                result.update(
                    frames=count,
                    duration=previous - result["video_start"] + 1 / max(result["fps"], 1),
                    max_video_backwards_seconds=backwards,
                )
        elif suffix not in {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}:
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
            if timeline:
                first_time = float(first_audio.time or 0)
                end = first_time + first_audio.samples / first_audio.sample_rate
                samples = first_audio.samples
                jumps = []
                for decoded_audio in media.decode(audio=0):
                    timestamp = float(decoded_audio.time) if decoded_audio.time is not None else end
                    delta = timestamp - end
                    if abs(delta) > 2 / decoded_audio.sample_rate:
                        jumps.append({"at": timestamp - first_time, "seconds": delta})
                    samples += decoded_audio.samples
                    end = timestamp + decoded_audio.samples / decoded_audio.sample_rate
                result["audio"].update(
                    duration=end - first_time, decoded_seconds=samples / first_audio.sample_rate, timestamp_jumps=jumps
                )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--thumbnail", type=Path)
    parser.add_argument("--timeline", action="store_true")
    parser.add_argument("--extension")
    args = parser.parse_args()
    try:
        result = {"valid": True, "metadata": inspect(args.path, args.thumbnail, args.timeline, args.extension)}
    except Exception as error:
        result = {
            "valid": False,
            "error": f"Media cannot be decoded: {type(error).__name__}. Re-encode or exclude this item.",
        }
    print("MUSUBI_MEDIA=" + json.dumps(result))


if __name__ == "__main__":
    main()

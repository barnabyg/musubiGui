"""Bounded CPU conversion of one H3 clip; invoked in an isolated subprocess."""

import argparse
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
from PIL import Image, ImageOps


def prepare_video(source: Path, output: Path, width: int, height: int, frames: int, fit: str) -> float:
    duration = Fraction(frames, 24)
    count = 0
    with av.open(str(source)) as media, av.open(str(output), "w") as target:
        video = media.streams.video[0]
        video.thread_count = 2
        stream: Any = target.add_stream("libx264", rate=24)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "18", "preset": "fast", "threads": "2"}
        origin: float | None = None
        previous: Any = None
        previous_time = 0.0
        end = 0.0

        def emit(boundary: float) -> None:
            nonlocal count
            if previous is None or count / 24 >= boundary or count >= frames:
                return
            image = previous.to_image()
            if previous.rotation:
                image = image.rotate(previous.rotation, expand=True)
            image = (
                ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
                if fit == "crop"
                else ImageOps.pad(image, (width, height), method=Image.Resampling.LANCZOS, color="black")
            )
            while count < frames and count / 24 < boundary - 1e-8:
                frame_type: Any = av.VideoFrame
                frame = frame_type.from_image(image)
                frame.pts, frame.time_base = count, Fraction(1, 24)
                for packet in stream.encode(frame):
                    target.mux(packet)
                count += 1

        for decoded in media.decode(video=0):
            if decoded.time is None:
                raise ValueError("Video has no frame timestamps; re-encode its timeline first.")
            if origin is None:
                origin = float(decoded.time)
            timestamp = float(decoded.time) - origin
            if timestamp < previous_time - 1e-6:
                raise ValueError("Video timestamps move backwards; re-encode its timeline first.")
            emit(timestamp)
            previous, previous_time = decoded, timestamp
            frame_duration = (
                float(decoded.duration * decoded.time_base)
                if decoded.duration and decoded.time_base
                else 1 / float(video.average_rate or 24)
            )
            end = timestamp + frame_duration
            if timestamp >= float(duration):
                break
        if end + 1e-6 < float(duration):
            raise ValueError(f"Video is too short: needs at least {float(duration):.3f} seconds. No padding was added.")
        emit(float(duration))
        if count != frames or origin is None:
            raise ValueError("Video did not produce the required training frames.")
        for packet in stream.encode():
            target.mux(packet)
    return origin


def prepare_audio(source: Path, output: Path, frames: int, origin: float | None) -> None:
    """Keep timestamps on the video grid; reject material gaps rather than invent supervision."""
    samples = round(frames / 24 * 32000)
    data = bytearray(samples * 4)
    covered = bytearray(samples)
    resampler: Any = av.AudioResampler(format="s16", layout="stereo", rate=32000)
    previous_end: int | None = None
    with av.open(str(source)) as media:
        if not media.streams.audio:
            raise ValueError("Selected audio source has no audio stream.")
        for decoded in media.decode(audio=0):
            if origin is None:
                origin = float(decoded.time or 0)
            converted = resampler.resample(decoded)
            for frame in converted:
                if frame.time is None:
                    raise ValueError("Audio has no timestamps.")
                start = round((float(frame.time) - origin) * 32000)
                if previous_end is not None and abs(start - previous_end) > 1600:
                    raise ValueError("Audio timeline has a discontinuity larger than 50 ms.")
                previous_end = start + frame.samples
                low, high = max(0, start), min(samples, previous_end)
                if high > low:
                    raw = bytes(frame.planes[0])
                    data[low * 4 : high * 4] = raw[(low - start) * 4 : (high - start) * 4]
                    covered[low:high] = b"\1" * (high - low)
            if previous_end is not None and previous_end >= samples:
                break
        # Flush the resampler's delayed tail (important when converting 44.1/48 kHz sources).
        for frame in resampler.resample(None):
            start = round((float(frame.time or 0) - (origin or 0)) * 32000)
            low, high = max(0, start), min(samples, start + frame.samples)
            if high > low:
                data[low * 4 : high * 4] = bytes(frame.planes[0])[(low - start) * 4 : (high - start) * 4]
                covered[low:high] = b"\1" * (high - low)
    if samples - sum(covered) > 6400:
        raise ValueError("Audio is short or misaligned by more than 200 ms in this training window.")
    with wave.open(str(output), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(32000)
        target.writeframes(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--fit", choices=["crop", "pad"], required=True)
    parser.add_argument("--audio", type=Path)
    args = parser.parse_args()
    try:
        origin = prepare_video(args.source, args.output, args.width, args.height, args.frames, args.fit)
        if args.audio:
            prepare_audio(
                args.audio, args.output.with_suffix(".wav"), args.frames, origin if args.audio == args.source else None
            )
    except Exception as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()

"""Run qualified standalone generation, with one fresh-process CUDA capacity retry."""

import argparse
import json
import os
import signal
import subprocess  # nosec B404
import sys
from pathlib import Path
from types import FrameType


def _has_output(args: list[str]) -> bool:
    if "--save_path" not in args:
        return False
    destination = Path(args[args.index("--save_path") + 1])
    if destination.suffix:
        return destination.is_file() and destination.stat().st_size > 0
    return destination.exists() and any(
        file.is_file() and file.suffix.lower() in {".mp4", ".png", ".safetensors"} for file in destination.iterdir()
    )


def generate(args: list[str], fallback_blocks: int) -> int:
    if "--blocks_to_swap" not in args:
        raise ValueError("Frozen generation command is missing its memory setting.")
    index = args.index("--blocks_to_swap") + 1
    initial = int(args[index])
    stopped = False
    process: subprocess.Popen[str] | None = None

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopped
        stopped = True
        # Windows broadcasts CTRL_BREAK to the stage's process group, including the child.
        if os.name != "nt" and process is not None and process.poll() is None:
            process.send_signal(signum)

    signals = [signal.SIGINT, *([signal.SIGBREAK] if os.name == "nt" else [signal.SIGTERM])]
    handlers = {signum: signal.signal(signum, stop) for signum in signals}
    try:
        for attempt in range(2):
            if stopped:
                return 130
            capacity_failure = False
            print(
                "MUSUBI_PREVIEW_MEMORY=" + json.dumps({"attempt": attempt + 1, "blocks_to_swap": int(args[index])}),
                flush=True,
            )
            # B603: executes only the validated frozen upstream script and argument array, without a shell.
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-B", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )  # nosec B603
            try:
                if process.stdout:
                    for line in process.stdout:
                        print(line, end="", flush=True)
                        lower = line.lower()
                        capacity_failure |= "cuda out of memory" in lower or "cuda error: out of memory" in lower
                code = process.wait()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=30)
                if process.stdout:
                    process.stdout.close()
            if (
                code == 0
                or stopped
                or not capacity_failure
                or attempt > 0
                or fallback_blocks <= initial
                or _has_output(args)
            ):
                return 130 if stopped else code
            # The failed CUDA process has exited, releasing its model, allocator and decoder allocations.
            print(
                "Preview exceeded GPU capacity; the failed process has exited. "
                "Retrying once with the conservative family preset.",
                flush=True,
            )
            args = list(args)
            args[index] = str(fallback_blocks)
        return 1
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fallback-blocks", type=int, required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    args = options.args[1:] if options.args[:1] == ["--"] else options.args
    if not args or not 0 <= options.fallback_blocks <= 48:
        parser.error("A frozen upstream command and legal conservative block count are required.")
    raise SystemExit(generate(args, options.fallback_blocks))


if __name__ == "__main__":
    main()

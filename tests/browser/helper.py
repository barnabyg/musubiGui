"""The Python that browser tests run: a backend they can crash, and fixtures they can't make from Node."""

import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path

import psutil
import uvicorn

from musubi_gui.acceptance import synthetic_video
from musubi_gui.app import create_app
from musubi_gui.run_store import DeviceLease

OTHER_INSTANCE_RUN = "other-work"


def serve(args: argparse.Namespace) -> None:
    # The state directory comes from MUSUBI_GUI_STATE, as it does for the real entry point.
    process = psutil.Process()
    args.identity.write_text(json.dumps({"pid": process.pid, "created": process.create_time(), "exe": process.exe()}))
    app = create_app(fixture_mode=True, device_path=args.device)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


def kill(args: argparse.Namespace) -> None:
    # Kills only the recorded service, preserving its training children for recovery.
    identity = json.loads(args.identity.read_text())
    process = psutil.Process(identity["pid"])
    if process.create_time() != identity["created"] or process.exe() != identity["exe"]:
        raise SystemExit("The recorded backend process is gone.")
    process.kill()


def lease(args: argparse.Namespace) -> None:
    device = DeviceLease(args.state, args.device)
    if args.action == "acquire":
        device.acquire(OTHER_INSTANCE_RUN, {})
    else:
        device.release(OTHER_INSTANCE_RUN)


def hold_file(args: argparse.Namespace) -> None:
    # An open handle stops Windows deleting the file, until stdin closes or this process is killed.
    with args.file.open("rb"):
        print("ready", flush=True)
        sys.stdin.read()


def video(args: argparse.Namespace) -> None:
    synthetic_video(args.file, args.frames, rate=Fraction(args.fps))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)

    command = commands.add_parser("serve")
    command.add_argument("--identity", type=Path, required=True)
    command.add_argument("--device", type=Path, required=True)
    command.add_argument("--port", type=int, required=True)
    command.set_defaults(run=serve)

    command = commands.add_parser("kill")
    command.add_argument("identity", type=Path)
    command.set_defaults(run=kill)

    command = commands.add_parser("lease")
    command.add_argument("action", choices=["acquire", "release"])
    command.add_argument("state", type=Path)
    command.add_argument("device", type=Path)
    command.set_defaults(run=lease)

    command = commands.add_parser("hold-file")
    command.add_argument("file", type=Path)
    command.set_defaults(run=hold_file)

    command = commands.add_parser("video")
    command.add_argument("file", type=Path)
    command.add_argument("frames", type=int)
    command.add_argument("fps", type=int)
    command.set_defaults(run=video)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()

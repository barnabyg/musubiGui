"""Loopback-only launcher."""

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from musubi_gui.app import create_app
from musubi_gui.launcher import RESTART


def serve(port: int, *, fixture_mode: bool = False, device_path: Path | None = None, assets: Path | None = None) -> int:
    restarting = False

    def restart() -> None:
        nonlocal restarting
        restarting = True
        server.should_exit = True

    app = create_app(fixture_mode=fixture_mode, device_path=device_path, assets=assets, restart=restart)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, timeout_graceful_shutdown=600)
    )
    server.run()
    return RESTART if restarting else 0 if server.started else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch local Musubi GUI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--acceptance-fixture",
        action="store_true",
        help="Explicitly enable synthetic-worker acceptance fixtures; never qualifies GPU support.",
    )
    args = parser.parse_args()
    if not args.no_browser:
        timer = threading.Timer(1.5, webbrowser.open, args=[f"http://127.0.0.1:{args.port}"])
        timer.daemon = True
        timer.start()
    raise SystemExit(serve(args.port, fixture_mode=args.acceptance_fixture))


if __name__ == "__main__":
    main()

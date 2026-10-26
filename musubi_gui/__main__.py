"""Loopback-only launcher."""

import argparse
import threading
import webbrowser

import uvicorn

from musubi_gui.app import create_app


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
    uvicorn.run(create_app(fixture_mode=args.acceptance_fixture), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

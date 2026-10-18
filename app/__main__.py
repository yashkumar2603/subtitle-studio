"""Start Subtitle Studio: ``python -m app [--port 7860] [--no-browser]``."""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import webbrowser


def _free_port(host: str, start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name != "nt":
                # Match uvicorn, so a port lingering in TIME_WAIT counts as free.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found")


def main() -> None:
    ap = argparse.ArgumentParser(description="Subtitle Studio")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost only; other values let anyone on that network open files on this PC)")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    import uvicorn

    port = _free_port(args.host, args.port)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{port}"
    print(f"\n  Subtitle Studio running at {url}\n  Close this window (or press Ctrl+C) to stop.\n", flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("app.main:app", host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

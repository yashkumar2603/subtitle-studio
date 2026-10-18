"""Start Subtitle Studio: ``python -m app [--port 7860] [--no-browser]``."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
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

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):  # Ctrl+C / SIGTERM / Ctrl+Break (Windows)
            _stop()

    server = Server(uvicorn.Config("app.main:app", host=args.host, port=port, log_level="warning"))
    if hasattr(signal, "SIGHUP"):  # terminal window closed (Linux/macOS)
        signal.signal(signal.SIGHUP, lambda *_: _stop())
    server.run()
    _stop()


def _stop() -> None:
    """Exit now instead of waiting on open video streams.

    uvicorn's graceful shutdown waits for every connection to finish, and the
    browser keeps the video stream open, so Ctrl+C used to hang until a second
    Ctrl+C killed requests mid-flight with tracebacks. Nothing needs a graceful
    drain here: saves are atomic, ffmpeg children are killed (and on Windows also
    tied to a kill-on-close job object), and process exit frees RAM and GPU memory.
    The Whisper model stays in the Hugging Face cache for the next start.
    """
    print("\n  Stopping Subtitle Studio... bye!", flush=True)
    try:
        from .main import shutdown

        shutdown()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()

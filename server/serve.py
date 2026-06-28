#!/usr/bin/env python3
# coding: utf-8
"""Local dev server for Ledger Explorer (static + CSV data).

Features:
- Serves files under --root (default: current directory)
- Adds permissive CORS headers for local development
- Disables caching to avoid stale CSV while iterating

Usage:
  python server/serve.py --root .
  open http://localhost:8000/web/
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # keep stdout quieter; comment out if you want logs
        return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Repository root to serve")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    # Python 3.11+: directory argument is available on handler
    handler = lambda *a, **kw: Handler(*a, directory=str(root), **kw)  # noqa: E731

    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"Serving {root} on http://localhost:{args.port}/ (Ctrl+C to stop)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

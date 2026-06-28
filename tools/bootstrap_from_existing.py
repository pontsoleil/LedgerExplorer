#!/usr/bin/env python3
# coding: utf-8
"""Bootstrap this repository from an existing deployed directory.

This script copies:
  - index.html/app.js/app.css (and other static files) -> web/
  - server data (CSV/JSON) -> data/full/
  - optional i18n resources -> web/i18n/

It is intentionally conservative: it will NOT delete destination files, and it
will skip files that look like secrets (e.g., *.pem, *.key, .env).

Usage example:
  python tools/bootstrap_from_existing.py --source /var/www/html/ledger --dest .

After copying, you should:
  1) move a SMALL subset to data/sample/ for public commit
  2) keep data/full/ ignored (or store via LFS/Releases)
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

SKIP_EXT = {".pem", ".key", ".p12", ".pfx"}
SKIP_NAMES = {".env", "env", "secrets.json", "id_rsa"}

STATIC_HINTS = {"index.html", "app.js", "app.css"}


def should_skip(p: Path) -> bool:
    if p.name in SKIP_NAMES:
        return True
    if p.suffix.lower() in SKIP_EXT:
        return True
    return False


def copy_tree(src: Path, dst: Path) -> None:
    for root, dirs, files in os.walk(src):
        root_p = Path(root)
        # skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            sp = root_p / fn
            if should_skip(sp):
                continue
            rel = sp.relative_to(src)
            tp = dst / rel
            tp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, tp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Existing deployed ledger directory")
    ap.add_argument("--dest", required=True, help="Destination repo root (.)")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()

    if not src.exists():
        raise SystemExit(f"source not found: {src}")
    if not dest.exists():
        raise SystemExit(f"dest not found: {dest}")

    web_dst = dest / "web"
    data_dst = dest / "data" / "full"

    web_dst.mkdir(parents=True, exist_ok=True)
    data_dst.mkdir(parents=True, exist_ok=True)

    # Copy static files (best effort): if your deployed dir already contains web root, copy all
    copy_tree(src, web_dst)

    # If there is a 'server/data' style folder inside, copy it to data/full
    for candidate in [src / "server" / "data", src / "data", src / "server_data"]:
        if candidate.exists() and candidate.is_dir():
            copy_tree(candidate, data_dst)

    print("Done.")
    print(f"Static copied into: {web_dst}")
    print(f"Data copied into:   {data_dst}")
    print("Next: move a small subset into data/sample/ and keep data/full/ out of Git history.")


if __name__ == "__main__":
    main()

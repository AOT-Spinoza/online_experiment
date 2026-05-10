#!/usr/bin/env python3
"""
One-shot production deploy build.

Regenerates experiment/public/stimuli.json with absolute production URLs
(read from PUBLIC_BASE_URL in .env), runs `npm run build`, and zips up
experiment/dist/ as aot-experiment.zip ready to upload to JATOS.

This is a guard against the easy mistake of running `pipeline/dev_link.py`
(which writes relative `_videos/...` URLs into the same file) and then
deploying that bundle — the videos would all 404 in production.

Usage:

    python pipeline/deploy.py                     # full chain
    python pipeline/deploy.py --skip-zip          # just stimuli.json + npm build
    python pipeline/deploy.py --base-url https://example.com/videos
                                                  # override the URL from .env
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "error: python-dotenv not installed. install with:\n"
        "    python -m pip install -r pipeline/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kwargs) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(REPO_ROOT), **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url", default=None,
        help="Override the PUBLIC_BASE_URL value from .env.",
    )
    parser.add_argument(
        "--zip-name", default="aot-experiment.zip",
        help="Output zip filename (placed at repo root). (default: %(default)s)",
    )
    parser.add_argument("--skip-zip", action="store_true",
        help="Don't produce the zip; stop after npm run build.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    base_url = args.base_url or os.environ.get("PUBLIC_BASE_URL")
    if not base_url:
        print(
            "error: PUBLIC_BASE_URL not set in .env (and --base-url not given).\n"
            "       set it to e.g. https://pub-<hash>.r2.dev",
            file=sys.stderr,
        )
        return 2

    public_out = REPO_ROOT / "experiment" / "public" / "stimuli.json"

    print("=" * 60)
    print(f"  base URL: {base_url}")
    print(f"  manifest: {public_out.relative_to(REPO_ROOT)}")
    print(f"  zip:      {args.zip_name if not args.skip_zip else '(skipped)'}")
    print("=" * 60)
    print()

    # Step 1: regenerate manifest with production URLs.
    print("[1/3] regenerate stimuli.json with production URLs")
    rc = run([
        sys.executable, str(REPO_ROOT / "pipeline" / "build_manifest.py"),
        "--base-url", base_url,
        "--public-out", str(public_out),
    ])
    if rc != 0:
        print("error: build_manifest.py failed.", file=sys.stderr)
        return rc
    print()

    # Step 2: npm run build.
    print("[2/3] npm run build")
    rc = run(["npm", "run", "build"], cwd=str(REPO_ROOT / "experiment"))
    if rc != 0:
        print("error: npm run build failed.", file=sys.stderr)
        return rc
    print()

    if args.skip_zip:
        print("done — dist/ is ready (zip skipped).")
        return 0

    # Step 3: zip dist/ for JATOS.
    print("[3/3] zip experiment/dist/ for JATOS upload")
    zip_path = REPO_ROOT / args.zip_name
    if zip_path.exists():
        zip_path.unlink()
    rc = run(
        ["zip", "-r", str(zip_path), "."],
        cwd=str(REPO_ROOT / "experiment" / "dist"),
    )
    if rc != 0:
        print("error: zip failed.", file=sys.stderr)
        return rc

    # Sanity-check the zip contains stimuli.json.
    rc = subprocess.call(
        ["unzip", "-l", str(zip_path)],
        cwd=str(REPO_ROOT),
    )

    print()
    print(f"done — upload {zip_path.relative_to(REPO_ROOT)} to JATOS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

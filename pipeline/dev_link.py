#!/usr/bin/env python3
"""
Wire up the experiment for local development with real video stimuli.

Run this once after the pipeline has produced its outputs (transcoded +
hashed videos, the public manifest). It:

  1. Symlinks pipeline/staging_hashed/  →  experiment/public/_videos/
     so the dev server (esbuild --serve from dist/) finds the videos at
     /_videos/<hash>.mp4. The symlink target is a relative path so it
     survives moves/clones of the project.

  2. Re-runs build_manifest.py with --base-url '_videos' and writes the
     public manifest to experiment/public/stimuli.json, so the URLs in
     the manifest resolve correctly under the dev server.

After this:
    cd experiment && npm run dev
    → http://localhost:3000/   loads stimuli.json and plays real clips.

Re-run any time the pipeline outputs change. Idempotent — replaces
existing symlinks/files.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HASHED_DIR = REPO_ROOT / "pipeline" / "staging_hashed"
DEFAULT_PUBLIC_DIR = REPO_ROOT / "experiment" / "public"
DEFAULT_LINK_NAME = "_videos"


def make_symlink(src_dir: Path, dst_link: Path) -> None:
    """Create or replace a symlink dst_link → src_dir.

    The link is stored as a *relative* path so the project can be moved
    without breaking the link.
    """
    if not src_dir.exists():
        raise FileNotFoundError(
            f"source directory does not exist: {src_dir}\n"
            f"have you run `python pipeline/transcode.py` yet?"
        )
    dst_link.parent.mkdir(parents=True, exist_ok=True)
    if dst_link.is_symlink() or dst_link.exists():
        dst_link.unlink()
    rel_target = os.path.relpath(src_dir, dst_link.parent)
    os.symlink(rel_target, dst_link)
    print(f"  symlink:  {dst_link.relative_to(REPO_ROOT)}  →  {rel_target}")


def run_build_manifest(base_url: str, public_out: Path) -> int:
    """Invoke build_manifest.py with the right flags."""
    script = REPO_ROOT / "pipeline" / "build_manifest.py"
    cmd = [
        sys.executable, str(script),
        "--base-url", base_url,
        "--public-out", str(public_out),
        # Private manifest stays at the default location (secrets/).
    ]
    print(f"  running:  {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hashed-dir", type=Path, default=DEFAULT_HASHED_DIR,
        help="Source dir of hashed-name MP4s. (default: %(default)s)",
    )
    parser.add_argument(
        "--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR,
        help="Experiment's public/ dir to write into. (default: %(default)s)",
    )
    parser.add_argument(
        "--link-name", default=DEFAULT_LINK_NAME,
        help="Symlink filename inside public/ that points at the videos. "
             "(default: %(default)s) — also used as the manifest's --base-url",
    )
    args = parser.parse_args()

    hashed_dir = args.hashed_dir.resolve()
    public_dir = args.public_dir.resolve()
    link = public_dir / args.link_name
    stimuli_json = public_dir / "stimuli.json"

    print("wiring up local dev environment:")
    print(f"  hashed videos: {hashed_dir}")
    print(f"  public dir:    {public_dir}")
    print(f"  link name:     {args.link_name}")
    print()

    # Step 1: symlink
    try:
        make_symlink(hashed_dir, link)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Step 2: build manifest with the right base URL
    print()
    rc = run_build_manifest(base_url=args.link_name, public_out=stimuli_json)
    if rc != 0:
        print("error: build_manifest.py failed; see output above.", file=sys.stderr)
        return rc

    print()
    print("done. local dev is wired up.")
    print()
    print("  cd experiment && npm run dev")
    print("  → http://localhost:3000/")
    print()
    print(
        f"  re-run `python pipeline/dev_link.py` whenever the pipeline outputs change.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Transcode source videos for the Arrow-of-Time online experiment.

Two-stage pipeline:

  1. Transcode every source video to web-friendly H.264 MP4 at reduced
     resolution. Output is written to --staging with the source's relative
     path preserved (file extension always becomes .mp4). This is the
     paper-trail copy with original filenames intact.

  2. Make a hashed-filename deployment copy of the staging output at
     --hashed, where every file is renamed to a fresh random hex ID. This
     is the version uploaded to the streaming host so URLs leak no
     direction information. A TSV mapping hashed_filename -> original_path
     is written to --tsv (treat this as the private manifest; gitignore it).

Re-runs are idempotent:
  - Stage 1 skips files whose staging output already exists.
  - Stage 2 reuses hash assignments from an existing TSV, so re-running
    only adds newly-introduced sources without reshuffling prior IDs.

Encoding defaults (all overridable):
  container:   MP4
  video:       libx264, yuv420p
  preset:      slow            (one-time encode; favor compression)
  CRF:         28              (visually OK at small sizes; raise for smaller files)
  short-side:  480 px          (height; assumes landscape source)
  audio:       dropped (-an)   (irrelevant for AoT, reversed audio is unnatural)
  streaming:   +faststart      (start playback before full download)

Typical use (run from repo root):

  python pipeline/transcode.py \\
      --source ./pipeline/source \\
      --staging ./pipeline/staging \\
      --hashed ./pipeline/staging_hashed \\
      --tsv ./secrets/hash_map.tsv

Requirements:
  - ffmpeg on PATH (only the `ffmpeg` binary is used; ffprobe is not required)
  - python 3.10+
  - tqdm  (see pipeline/requirements.txt)
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import secrets
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from tqdm import tqdm
except ImportError:
    print(
        "error: tqdm not installed.\n"
        "       run: pip install -r pipeline/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


# Source files with these extensions are picked up. Output is always .mp4.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


@dataclass(frozen=True)
class TranscodeJob:
    source: Path     # absolute path to the source file
    staging: Path    # absolute path where the transcoded output will land (always .mp4)
    rel: Path        # source path relative to --source (for the TSV)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def posix_str(p: Path) -> str:
    """Render a relative path with forward slashes regardless of host OS."""
    return str(p).replace(os.sep, "/")


def check_ffmpeg() -> None:
    """Bail out early with a clear message if ffmpeg isn't available."""
    if shutil.which("ffmpeg") is None:
        print(
            "error: ffmpeg not found on PATH.\n"
            "       install it first (e.g. 'brew install ffmpeg' or 'apt install ffmpeg').",
            file=sys.stderr,
        )
        sys.exit(2)


def find_videos(root: Path, exclude: Iterable[Path]) -> list[Path]:
    """Recursively find video files under root, excluding any inside `exclude` dirs."""
    excluded_resolved = [d.resolve() for d in exclude if d.exists()]

    def is_excluded(p: Path) -> bool:
        rp = p.resolve()
        return any(rp.is_relative_to(e) for e in excluded_resolved)

    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and not is_excluded(p)
    )


def staging_path_for(staging_dir: Path, rel_source: Path) -> Path:
    """Always force .mp4 extension regardless of source extension."""
    return staging_dir / rel_source.with_suffix(".mp4")


# ---------------------------------------------------------------------------
# Stage 1: transcode
# ---------------------------------------------------------------------------


def build_ffmpeg_cmd(
    src: Path,
    dst: Path,
    *,
    short_side: int,
    crf: int,
    preset: str,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",                           # caller handles skip-if-exists
        "-loglevel", "error",           # quiet on success
        "-i", str(src),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        # scale: set height to N, width auto (-2 keeps result divisible by 2,
        # required by yuv420p). NOTE: assumes a landscape source — for the AoT
        # corpus that's uniformly true. A portrait source would end up scaled
        # to N on its long side, not its short side; revisit if mixed
        # orientations show up.
        "-vf", f"scale=-2:{short_side}",
        "-an",                          # drop audio entirely
        "-movflags", "+faststart",      # streaming-friendly mp4 layout
        str(dst),
    ]


def transcode_one(
    job: TranscodeJob,
    *,
    short_side: int,
    crf: int,
    preset: str,
    overwrite: bool,
) -> tuple[str, str | None]:
    """Run ffmpeg on one job. Returns (rel_posix, error_or_None)."""
    rel_posix = posix_str(job.rel)
    job.staging.parent.mkdir(parents=True, exist_ok=True)
    if job.staging.exists() and not overwrite:
        return rel_posix, None
    cmd = build_ffmpeg_cmd(
        job.source, job.staging, short_side=short_side, crf=crf, preset=preset
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return rel_posix, None
    except subprocess.CalledProcessError as e:
        # remove any partial output so a future re-run starts clean
        try:
            job.staging.unlink()
        except FileNotFoundError:
            pass
        msg = (e.stderr or "").strip()
        last_line = msg.splitlines()[-1] if msg else "ffmpeg failed"
        return rel_posix, last_line
    except Exception as e:  # noqa: BLE001 - surface any unexpected failure to the report
        return rel_posix, f"unexpected: {e!r}"


# ---------------------------------------------------------------------------
# Stage 2: hash + copy + tsv
# ---------------------------------------------------------------------------


def random_id(byte_len: int) -> str:
    return secrets.token_hex(byte_len)


def assign_hashes(
    rels: Iterable[Path],
    used: set[str],
    byte_len: int,
) -> dict[Path, str]:
    """Give each rel a fresh random hex ID. Loops on collisions (effectively never)."""
    out: dict[Path, str] = {}
    for rel in rels:
        while True:
            hid = random_id(byte_len)
            if hid not in used:
                used.add(hid)
                out[rel] = hid
                break
    return out


def load_tsv(path: Path) -> dict[Path, str]:
    """Load existing hash_map.tsv into {rel_path: hex_id} (extension stripped from id)."""
    if not path.exists():
        return {}
    out: dict[Path, str] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            out[Path(row["original_path"])] = Path(row["hashed_filename"]).stem
    return out


def write_tsv(path: Path, rows: list[tuple[str, str]]) -> None:
    """Write the hashed_filename -> original_path TSV (sorted for stable diffs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r[1])
    with path.open("w", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(["hashed_filename", "original_path"])
        for hashed, original in rows:
            writer.writerow([hashed, original])


# ---------------------------------------------------------------------------
# CLI / orchestration
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("videos/rescaled_final"),
        help="Input directory of source videos; recurses into subfolders. (default: %(default)s)",
    )
    parser.add_argument(
        "--staging",
        type=Path,
        default=Path("pipeline/staging"),
        help="Output dir for transcoded paper-trail copy with original filenames. (default: %(default)s)",
    )
    parser.add_argument(
        "--hashed",
        type=Path,
        default=Path("pipeline/staging_hashed"),
        help="Output dir for the hashed-filename deployment copy. (default: %(default)s)",
    )
    parser.add_argument(
        "--tsv",
        type=Path,
        default=Path("secrets/hash_map.tsv"),
        help="Path for the hash -> original-path TSV. Treat as private. (default: %(default)s)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 2) // 2),
        help="Parallel ffmpeg workers (default: half of CPU cores)",
    )
    parser.add_argument(
        "--short-side",
        type=int,
        default=480,
        help="Target short-side resolution in pixels (default: %(default)s)",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=28,
        help="x264 CRF; lower = better quality, larger files (default: %(default)s)",
    )
    parser.add_argument(
        "--preset",
        default="slow",
        help="x264 preset: ultrafast | superfast | veryfast | faster | fast | medium | slow | slower | veryslow (default: %(default)s)",
    )
    parser.add_argument(
        "--hash-bytes",
        type=int,
        default=8,
        help="Random ID length in bytes; filename will be 2*N hex chars (default: 8 -> 16 hex chars)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only N source files, randomly sampled with --seed (use for pilot runs)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for --limit sampling. Same seed -> same subset. (default: %(default)s)",
    )
    parser.add_argument(
        "--overwrite-transcode",
        action="store_true",
        help="Re-encode files even if the staging output already exists",
    )
    parser.add_argument(
        "--overwrite-hashed",
        action="store_true",
        help="Rebuild the hashed dir from scratch (existing IDs are still reused from --tsv if present)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List actions without running them",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_ffmpeg()

    source_dir = args.source.resolve()
    staging_dir = args.staging.resolve()
    hashed_dir = args.hashed.resolve()
    tsv_path = args.tsv.resolve()

    # Sanity checks: the three dirs must be distinct, and the source dir must exist.
    if len({source_dir, staging_dir, hashed_dir}) < 3:
        print(
            "error: --source, --staging and --hashed must all be different paths.",
            file=sys.stderr,
        )
        return 2
    if not source_dir.is_dir():
        print(f"error: source directory does not exist: {source_dir}", file=sys.stderr)
        return 2

    videos = find_videos(source_dir, exclude=[staging_dir, hashed_dir])
    if not videos:
        print(f"error: no video files found under {source_dir}", file=sys.stderr)
        return 2

    total_found = len(videos)
    sample_note = ""
    if args.limit and args.limit < total_found:
        # Random sample with a fixed seed so pilot runs are reproducible.
        # Re-sort the subset to keep dry-run / progress output readable.
        rng = random.Random(args.seed)
        videos = sorted(rng.sample(videos, args.limit))
        sample_note = f"  (random sample of {args.limit} from {total_found}, seed={args.seed})"

    print(f"source:   {source_dir}  ({len(videos)} videos){sample_note}")
    print(f"staging:  {staging_dir}")
    print(f"hashed:   {hashed_dir}")
    print(f"tsv:      {tsv_path}")

    if args.dry_run:
        print("\ndry run — first 10 files that would be processed:")
        for src in videos[:10]:
            print(f"  {src.relative_to(source_dir)}")
        if len(videos) > 10:
            print(f"  ... and {len(videos) - 10} more")
        return 0

    jobs = [
        TranscodeJob(
            source=src,
            staging=staging_path_for(staging_dir, src.relative_to(source_dir)),
            rel=src.relative_to(source_dir),
        )
        for src in videos
    ]

    # ---- Stage 1: transcode ----
    print(
        f"\n[1/2] transcoding {len(jobs)} files "
        f"(jobs={args.jobs}, crf={args.crf}, short_side={args.short_side}, preset={args.preset}) ..."
    )

    errors: list[tuple[str, str]] = []

    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futures = [
            ex.submit(
                transcode_one,
                j,
                short_side=args.short_side,
                crf=args.crf,
                preset=args.preset,
                overwrite=args.overwrite_transcode,
            )
            for j in jobs
        ]
        with tqdm(total=len(futures), unit="file", smoothing=0.1, dynamic_ncols=True) as bar:
            for fut in as_completed(futures):
                rel, err = fut.result()
                if err:
                    errors.append((rel, err))
                bar.update(1)

    if errors:
        print(f"\n[1/2] {len(errors)} transcoding failures:", file=sys.stderr)
        for rel, msg in errors[:20]:
            print(f"  {rel}: {msg}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more", file=sys.stderr)
        print("\nfix and re-run; existing transcodes are preserved.", file=sys.stderr)
        return 1

    print(f"[1/2] done — {len(jobs)} transcoded files in {staging_dir}")

    # ---- Stage 2: hash + copy + write TSV ----
    print(f"\n[2/2] building hashed deployment copy at {hashed_dir} ...")
    if args.overwrite_hashed and hashed_dir.exists():
        shutil.rmtree(hashed_dir)
    hashed_dir.mkdir(parents=True, exist_ok=True)

    existing = load_tsv(tsv_path)              # {rel: hex_id}
    used_ids = set(existing.values())
    rels = [j.rel for j in jobs]
    new_assignments = assign_hashes(
        (r for r in rels if r not in existing),
        used=used_ids,
        byte_len=args.hash_bytes,
    )
    full_map: dict[Path, str] = {**existing, **new_assignments}

    rows: list[tuple[str, str]] = []
    copied = 0
    with tqdm(total=len(rels), unit="file", dynamic_ncols=True) as bar:
        for j in jobs:
            hashed_name = f"{full_map[j.rel]}.mp4"
            dst = hashed_dir / hashed_name
            if not dst.exists():
                shutil.copy2(j.staging, dst)
                copied += 1
            rows.append((hashed_name, posix_str(j.rel)))
            bar.update(1)

    write_tsv(tsv_path, rows)
    skipped = len(rows) - copied
    print(f"[2/2] done — {copied} new, {skipped} already-present")
    print(f"      tsv  -> {tsv_path}  ({len(rows)} entries)")
    print(f"      keep `{tsv_path.name}` private — it is the only mapping back to original filenames.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

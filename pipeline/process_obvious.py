#!/usr/bin/env python3
"""
Process new obvious-clip source videos for use as practice + qualification.

These are NEW videos (not in the main corpus), so unlike the legacy
"selection" workflow they need their own trim + reverse + transcode +
hash pipeline. After running this script the new clips are ready to be
picked up by `pipeline/dev_link.py` (which calls build_manifest.py) and
will appear in the public manifest's `practice` and `qualification`
arrays. They will NEVER appear in `main`, so there's no leak from
practice into the main task.

Pipeline stages (each visible under pipeline/source_obvious/):

  1. originals/                — your raw input videos (any resolution,
                                 any frame rate, any container)
  2. trimmed/                  — 2.5-s clips at the source's original
                                 resolution + codec, CRF 18 (high quality
                                 intermediate; useful for inspecting that
                                 the trim window is right)
  3. processed/practice/       — 480p / 24 fps / CRF 28 / no audio,
     processed/qualification/    encoded forward and reversed. Flat-named
                                 `<stem>_fw.mp4` / `<stem>_rv.mp4`.

After processing, the script also:

  4. assigns a fresh random hash to each of the 40 processed files,
     copies them into pipeline/staging_hashed/<hash>.mp4, and appends
     entries to secrets/hash_map.tsv with original_path of the form
     `practice/<stem>_fw.mp4` or `qualification/<stem>_rv.mp4`.

These TSV entries are picked up by build_manifest.py as practice +
qualification entries directly (since the path begins with practice/ or
qualification/), so they bypass the source_obvious main-pool selection
mechanism entirely. No leak risk: the new clips are never written into
public['main'].

Trim window:
  By default we trim each video to its CENTER 2.5 s. Override globally
  with --start-sec, or per-file via a JSON sidecar at
  pipeline/source_obvious/originals/start_times.json:
    { "input_filename.mp4": 1.5, ... }
  (values in seconds from start; missing keys → centered).

Practice/qualification split:
  Default: alphabetical sort of the input stems, first half → practice,
  second half → qualification (practice gets the extra on odd counts).
  Override by pre-organising into
    originals/practice/  and  originals/qualification/  subdirs.

Stem naming:
  We extract the leading numeric prefix (e.g. `12314192` from
  `12314192_3840_2160_30fps.mp4`) and use that as the clip's ID. Falls
  back to a slugified full stem if there's no leading number. The
  resulting hashed-name files in staging_hashed/ are URL-safe and don't
  collide with the main corpus's 4-digit IDs.

Re-runs are idempotent: existing intermediate and staged outputs are
skipped unless --overwrite. The TSV is appended to, not rewritten.

Usage (run from repo root):

  python pipeline/process_obvious.py
  python pipeline/process_obvious.py --start-sec 0
  python pipeline/process_obvious.py --preset fast
  python pipeline/process_obvious.py --overwrite
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
TARGET_SEC = 2.5
TARGET_FPS = 24
TARGET_SHORT_SIDE = 480
CRF_TRIM = 18      # intermediate; high quality so the final transcode has good source material
CRF_FINAL = 28     # match transcode.py / pipeline/transcode.py's main-corpus encoding

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = REPO_ROOT / "pipeline" / "source_obvious"
DEFAULT_STAGED = REPO_ROOT / "pipeline" / "staging_hashed"
DEFAULT_TSV = REPO_ROOT / "secrets" / "hash_map.tsv"

LEADING_DIGITS = re.compile(r"^(\d+)")
NON_FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def make_stem(filename: Path) -> str:
    """Pick a clean per-clip ID. Pexels-style names start with a numeric
    asset ID; we prefer that. Falls back to a slugified full stem when no
    leading number is present."""
    stem = filename.stem
    m = LEADING_DIGITS.match(stem)
    if m:
        return m.group(1)
    s = NON_FILENAME_SAFE.sub("_", stem).strip("_").lower()
    return s or "unnamed"


def find_inputs(originals_dir: Path) -> tuple[list[Path], dict[str, str]]:
    """Find input videos under originals_dir.

    Recognises both flat layouts (originals/*.mp4) and pre-organised
    layouts (originals/practice/*, originals/qualification/*). Returns
    (sorted list of input paths, dict of input_basename → forced-phase or
    None for auto-split).
    """
    if not originals_dir.is_dir():
        return [], {}

    forced_phase: dict[str, str | None] = {}
    inputs: list[Path] = []

    for phase_subdir in ("practice", "qualification"):
        sub = originals_dir / phase_subdir
        if sub.is_dir():
            for p in sub.iterdir():
                if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                    inputs.append(p)
                    forced_phase[p.name] = phase_subdir

    for p in originals_dir.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXT:
            inputs.append(p)
            forced_phase.setdefault(p.name, None)

    inputs.sort()
    return inputs, forced_phase


def get_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


def compute_start_time(
    input_path: Path,
    override: float | None,
    per_file: dict[str, float] | None,
) -> float:
    if per_file and input_path.name in per_file:
        return float(per_file[input_path.name])
    if override is not None:
        return float(override)
    duration = get_duration(input_path)
    if duration <= TARGET_SEC:
        return 0.0
    return (duration - TARGET_SEC) / 2.0


def run_ffmpeg(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else "ffmpeg failed")


def stage_trim(src: Path, dst: Path, *, start: float, preset: str) -> None:
    """Trim to TARGET_SEC starting at `start` (seconds). Output keeps the
    source's original resolution and frame rate (we re-encode at CRF 18
    for clean keyframe alignment + accurate timing)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-ss", f"{start:.3f}", "-t", f"{TARGET_SEC:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", preset, "-crf", str(CRF_TRIM),
        "-an",
        "-movflags", "+faststart",
        str(dst),
    ]
    run_ffmpeg(cmd)


def stage_transcode(src: Path, dst: Path, *, reverse: bool, preset: str) -> None:
    """Encode trimmed → TARGET_SHORT_SIDE / TARGET_FPS / CRF_FINAL,
    optionally reversing. The fps filter normalises framerate so all
    obvious clips match the main corpus's 24 fps regardless of source."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps={TARGET_FPS},scale=-2:{TARGET_SHORT_SIDE}"
    if reverse:
        vf += ",reverse"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", preset, "-crf", str(CRF_FINAL),
        "-vf", vf,
        "-an",
        "-movflags", "+faststart",
        str(dst),
    ]
    run_ffmpeg(cmd)


def load_hash_map(tsv_path: Path) -> tuple[dict[str, str], set[str]]:
    """Returns (existing original_path → hex_id mapping, set of used hex_ids)."""
    if not tsv_path.exists():
        return {}, set()
    path_to_hash: dict[str, str] = {}
    used: set[str] = set()
    with tsv_path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            hex_id = Path(row["hashed_filename"]).stem
            path_to_hash[row["original_path"]] = hex_id
            used.add(hex_id)
    return path_to_hash, used


def append_tsv_rows(tsv_path: Path, rows: list[tuple[str, str]]) -> None:
    if not rows:
        return
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not tsv_path.exists()
    with tsv_path.open("a", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        if is_new:
            writer.writerow(["hashed_filename", "original_path"])
        for hashed, original in rows:
            writer.writerow([hashed, original])


def fresh_hash(used: set[str], byte_len: int) -> str:
    while True:
        h = secrets.token_hex(byte_len)
        if h not in used:
            used.add(h)
            return h


def assign_phases(
    stems: list[str], forced: dict[str, str], stem_for_input: dict[Path, str]
) -> dict[str, str]:
    """Decide practice vs qualification per stem.

    `forced` maps an input-file basename to 'practice'/'qualification'
    when the user pre-organised originals/practice/ or
    originals/qualification/ subdirs. Stems referenced by forced
    assignments win; the rest are auto-split alphabetically (first half
    → practice).
    """
    # First respect any forced assignments.
    forced_for_stem: dict[str, str] = {}
    for input_path, stem in stem_for_input.items():
        f = forced.get(input_path.name)
        if f:
            forced_for_stem[stem] = f

    free_stems = sorted(s for s in stems if s not in forced_for_stem)
    n_practice = (len(free_stems) + 1) // 2
    auto = {s: "practice" for s in free_stems[:n_practice]}
    auto.update({s: "qualification" for s in free_stems[n_practice:]})

    out = {**auto, **forced_for_stem}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source-obvious-dir", type=Path, default=DEFAULT_BASE,
        help="(default: %(default)s)",
    )
    parser.add_argument(
        "--staging-hashed-dir", type=Path, default=DEFAULT_STAGED,
        help="Where hashed-filename copies are placed for the dev server. (default: %(default)s)",
    )
    parser.add_argument(
        "--tsv", type=Path, default=DEFAULT_TSV,
        help="hash_map.tsv to append new entries to. (default: %(default)s)",
    )
    parser.add_argument(
        "--start-sec", type=float, default=None,
        help="Global trim-start (seconds). Default: center each clip on its midpoint.",
    )
    parser.add_argument(
        "--preset", default="medium",
        help="x264 preset for both trim and final transcode. (default: %(default)s)",
    )
    parser.add_argument(
        "--hash-bytes", type=int, default=8,
        help="Random hash length in bytes (filename = 2*N hex chars). (default: 8 → 16 hex chars)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-process files even if their outputs already exist.",
    )
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("error: ffmpeg not on PATH.", file=sys.stderr)
        return 2
    if shutil.which("ffprobe") is None:
        print("error: ffprobe not on PATH.", file=sys.stderr)
        return 2

    base = args.source_obvious_dir.resolve()
    originals_dir = base / "originals"
    trimmed_dir = base / "trimmed"
    processed_dir = base / "processed"

    inputs, forced = find_inputs(originals_dir)
    if not inputs:
        print(f"error: no input videos found under {originals_dir}", file=sys.stderr)
        return 2

    # Optional sidecar with per-file start times
    per_file: dict[str, float] | None = None
    sidecar = originals_dir / "start_times.json"
    if sidecar.exists():
        per_file = json.loads(sidecar.read_text())
        print(f"using per-file start times from {sidecar.name} ({len(per_file)} entries)")

    # Stem assignment
    stem_for_input: dict[Path, str] = {p: make_stem(p) for p in inputs}
    all_stems = sorted(set(stem_for_input.values()))
    if len(all_stems) != len(set(stem_for_input.values())):
        # belt-and-braces; can't actually trigger since we just took a set
        print("error: duplicate stems in input set", file=sys.stderr)
        return 2

    phase_for_stem = assign_phases(all_stems, forced, stem_for_input)

    n_practice = sum(1 for v in phase_for_stem.values() if v == "practice")
    n_qualification = sum(1 for v in phase_for_stem.values() if v == "qualification")

    print(f"originals:    {originals_dir}  ({len(inputs)} videos)")
    print(f"intermediate: {trimmed_dir}/")
    print(f"final:        {processed_dir}/{{practice,qualification}}/")
    print(f"split:        {n_practice} → practice, {n_qualification} → qualification")
    print(f"target:       {TARGET_SEC}s @ {TARGET_FPS}fps, {TARGET_SHORT_SIDE}p, CRF {CRF_FINAL}")
    print()

    # ------ stage 1 + 2 + 3: trim, transcode forward, transcode reverse ------
    errors: list[tuple[str, str, str]] = []
    for i, src in enumerate(inputs, 1):
        stem = stem_for_input[src]
        phase = phase_for_stem[stem]
        t_path = trimmed_dir / f"{stem}.mp4"
        f_path = processed_dir / phase / f"{stem}_fw.mp4"
        r_path = processed_dir / phase / f"{stem}_rv.mp4"

        print(f"[{i:>2}/{len(inputs)}] {src.name}  →  stem={stem}, phase={phase}")
        try:
            if not t_path.exists() or args.overwrite:
                start = compute_start_time(src, args.start_sec, per_file)
                stage_trim(src, t_path, start=start, preset=args.preset)
                print(f"     trim    ok  (start={start:.2f}s)")
            else:
                print(f"     trim    skipped (exists)")

            if not f_path.exists() or args.overwrite:
                stage_transcode(t_path, f_path, reverse=False, preset=args.preset)
                print(f"     fwd     ok")
            else:
                print(f"     fwd     skipped (exists)")

            if not r_path.exists() or args.overwrite:
                stage_transcode(t_path, r_path, reverse=True, preset=args.preset)
                print(f"     rv      ok")
            else:
                print(f"     rv      skipped (exists)")
        except Exception as e:
            print(f"     FAIL: {e}", file=sys.stderr)
            errors.append((src.name, "process", str(e)))
            continue

    if errors:
        print()
        print(f"{len(errors)} processing errors; aborting before staging.", file=sys.stderr)
        for name, _, msg in errors[:10]:
            print(f"  {name}: {msg}", file=sys.stderr)
        return 1

    # ------ stage 4: hash + copy to staging_hashed/ + append to hash_map.tsv ------
    print()
    print(f"staging  → {args.staging_hashed_dir}")
    print(f"tsv      → {args.tsv}")

    args.staging_hashed_dir.mkdir(parents=True, exist_ok=True)
    path_to_hash, used_hashes = load_hash_map(args.tsv)

    new_rows: list[tuple[str, str]] = []
    n_copied = 0
    n_already = 0

    for src in inputs:
        stem = stem_for_input[src]
        phase = phase_for_stem[stem]
        for direction in ("fw", "rv"):
            processed_file = processed_dir / phase / f"{stem}_{direction}.mp4"
            if not processed_file.exists():
                continue  # earlier processing errored
            original_path = f"{phase}/{stem}_{direction}.mp4"

            existing_hash = path_to_hash.get(original_path)
            if existing_hash is not None:
                hex_id = existing_hash
            else:
                hex_id = fresh_hash(used_hashes, args.hash_bytes)
                new_rows.append((f"{hex_id}.mp4", original_path))

            dst = args.staging_hashed_dir / f"{hex_id}.mp4"
            if not dst.exists() or args.overwrite:
                shutil.copy2(processed_file, dst)
                n_copied += 1
            else:
                n_already += 1

    append_tsv_rows(args.tsv, new_rows)

    print(f"  staging_hashed: {n_copied} new, {n_already} already-present")
    print(f"  tsv:            {len(new_rows)} new entries appended")
    print()
    print("done.")
    print()
    print("next:")
    print("  python pipeline/dev_link.py    # rebuild manifests; new clips appear in")
    print("                                 # public.practice and public.qualification")
    return 0


if __name__ == "__main__":
    sys.exit(main())

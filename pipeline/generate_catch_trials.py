#!/usr/bin/env python3
"""
Generate the 10 catch-trial source videos for the AoT experiment.

Each catch trial is a 2.5-s, 24-fps, 480p MP4 showing two-line on-screen
text instructing the participant which direction key and which confidence
key to press, e.g.:

    Press FORWARD
    Then press 3

Files are written into the source directory (default: videos/rescaled_final/)
alongside the real source clips, with names that encode the (direction,
confidence) pair as a paper trail:

    catch_fwd_1.mp4 ... catch_fwd_5.mp4
    catch_rv_1.mp4  ... catch_rv_5.mp4

The downstream `transcode.py` then picks them up with the rest of the corpus
and produces hashed-filename copies; `build_manifest.py` parses these names
to derive metadata (`type=catch`, direction, expected_confidence) for the
private manifest. The public manifest just lists them under `main` with
URL + ID, indistinguishable from real clips by design (CLAUDE.md §2.7 / §3.9).

Re-runs are idempotent: existing files are not regenerated unless --overwrite.

Visual style (CLAUDE.md §2.7): white text on a dark-grey #1a1a1a background.
Two stacked centered lines. Same target encoding as `transcode.py` so the
double-encode is essentially a no-op (CRF 28 / yuv420p / +faststart / -an).

Usage (run from repo root):

    python pipeline/generate_catch_trials.py
    python pipeline/generate_catch_trials.py --output-dir videos/rescaled_final
    python pipeline/generate_catch_trials.py --preview-only catch_fwd_3
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Match transcode.py's encoding settings so the catch videos and the real
# corpus look identical in the player.
DEFAULT_WIDTH = 854          # 16:9 at 480p height
DEFAULT_HEIGHT = 480
DEFAULT_FPS = 24
DEFAULT_DURATION = 2.5       # seconds; 60 frames at 24 fps
DEFAULT_BG = "#1a1a1a"
DEFAULT_FG = "white"

# Font candidates, ordered by preference. We pick the first one that exists.
# The user can override with --font.
FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial.ttf",
    # Linux (Debian/Ubuntu paths via fontconfig packages)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

DIRECTIONS = [
    # (suffix-token, on-screen word)
    ("fwd", "FORWARD"),
    ("rv", "BACKWARD"),
]
CONFIDENCES = [1, 2, 3, 4, 5]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise RuntimeError(
        "no usable font found in standard locations. "
        "Pass --font /absolute/path/to/font.ttf"
    )


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        print(
            "error: ffmpeg not found on PATH. install it first (e.g. 'brew install ffmpeg').",
            file=sys.stderr,
        )
        sys.exit(2)


def filename_for(direction_token: str, confidence: int) -> str:
    return f"catch_{direction_token}_{confidence}.mp4"


def build_ffmpeg_cmd(
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    bg: str,
    fg: str,
    font_path: str,
    direction_word: str,
    confidence: int,
    crf: int,
    preset: str,
) -> list[str]:
    """ffmpeg invocation for one catch-trial video.

    Two stacked drawtext layers over a flat colored source. The first line
    ('Press FORWARD' or 'Press BACKWARD') is larger; the second line
    ('Then press N') is slightly smaller and placed below.

    Important: ffmpeg's filter-graph parser uses `:` to separate options
    inside a filter and `,` to chain filters. Any `:` inside an option
    *value* would terminate the value early, so we deliberately keep the
    text strings free of colons (and commas). No fancy escaping needed."""
    line1 = f"Press {direction_word}"
    line2 = f"Then press {confidence}"

    # Vertical layout: stack two text rows around the frame center, with
    # ~50 px of breathing room between them. drawtext's `text_h` evaluates
    # to the rendered text height at runtime, so this self-centers.
    fontsize_top = 64
    fontsize_bot = 48
    half_gap = 50

    # No quotes around fontfile= — macOS paths can contain spaces but never
    # colons, and ffmpeg's filter parser is happy with bare paths as long
    # as no `:` or `,` appears in the value. (Wrapping in single quotes can
    # actually break things in some ffmpeg builds.)
    drawtext_top = (
        f"drawtext="
        f"fontfile={font_path}:"
        f"text={line1}:"
        f"fontcolor={fg}:"
        f"fontsize={fontsize_top}:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2-{half_gap}"
    )
    drawtext_bot = (
        f"drawtext="
        f"fontfile={font_path}:"
        f"text={line2}:"
        f"fontcolor={fg}:"
        f"fontsize={fontsize_bot}:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2+{half_gap}"
    )

    return [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c={bg}:s={width}x{height}:r={fps}:d={duration}",
        "-vf", f"{drawtext_top},{drawtext_bot}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        "-an",
        "-movflags", "+faststart",
        str(output),
    ]


def render_one(
    output: Path,
    *,
    direction_token: str,
    direction_word: str,
    confidence: int,
    width: int,
    height: int,
    fps: int,
    duration: float,
    bg: str,
    fg: str,
    font_path: str,
    crf: int,
    preset: str,
    overwrite: bool,
) -> str | None:
    """Render one catch trial. Returns None on success, error string on failure."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        return None  # idempotent skip

    cmd = build_ffmpeg_cmd(
        output,
        width=width, height=height, fps=fps, duration=duration,
        bg=bg, fg=fg, font_path=font_path,
        direction_word=direction_word, confidence=confidence,
        crf=crf, preset=preset,
    )
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Clean up any partial file so a re-run is clean
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        msg = (e.stderr or "").strip().splitlines()
        return msg[-1] if msg else "ffmpeg failed"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("videos/rescaled_final"),
        help="Directory to write the catch-trial source files into. (default: %(default)s)",
    )
    parser.add_argument(
        "--font", type=str, default=None,
        help="Absolute path to a TTF/TTC/OTF font. If omitted, picks the first available "
             "system font from a built-in candidate list.",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
    parser.add_argument("--bg", default=DEFAULT_BG, help="Background color. (default: %(default)s)")
    parser.add_argument("--fg", default=DEFAULT_FG, help="Text color. (default: %(default)s)")
    parser.add_argument("--crf", type=int, default=28, help="x264 CRF (match transcode.py)")
    parser.add_argument("--preset", default="slow", help="x264 preset (match transcode.py)")
    parser.add_argument("--overwrite", action="store_true", help="Re-render even if output file exists")
    parser.add_argument(
        "--preview-only",
        metavar="STEM",
        help="Render only the named file (e.g. 'catch_fwd_3') for quick visual spot-check.",
    )
    args = parser.parse_args()

    check_ffmpeg()
    font_path = args.font or find_font()

    # Build the full set of (direction, confidence) jobs.
    jobs: list[tuple[str, str, int, Path]] = []
    for dtoken, dword in DIRECTIONS:
        for c in CONFIDENCES:
            name = filename_for(dtoken, c)
            jobs.append((dtoken, dword, c, args.output_dir / name))

    if args.preview_only:
        keep = args.preview_only
        if not keep.endswith(".mp4"):
            keep = keep + ".mp4"
        jobs = [j for j in jobs if j[3].name == keep]
        if not jobs:
            print(f"error: no catch-trial filename matches '{args.preview_only}'", file=sys.stderr)
            return 2

    print(f"font:        {font_path}")
    print(f"output dir:  {args.output_dir.resolve()}")
    print(f"jobs:        {len(jobs)}")
    print()

    errors: list[tuple[str, str]] = []
    for dtoken, dword, c, output in jobs:
        rel = output.name
        action = "skip" if (output.exists() and not args.overwrite) else "render"
        print(f"  [{action}] {rel}")
        if action == "skip":
            continue
        err = render_one(
            output,
            direction_token=dtoken,
            direction_word=dword,
            confidence=c,
            width=args.width, height=args.height, fps=args.fps, duration=args.duration,
            bg=args.bg, fg=args.fg, font_path=font_path,
            crf=args.crf, preset=args.preset,
            overwrite=args.overwrite,
        )
        if err:
            errors.append((rel, err))
            print(f"     FAIL: {err}", file=sys.stderr)

    print()
    if errors:
        print(f"{len(errors)} render failures.", file=sys.stderr)
        return 1
    print(f"done. {len(jobs)} catch-trial files in {args.output_dir.resolve()}")
    print(
        "next: re-run pipeline/transcode.py to pick these up alongside the real corpus, "
        "then pipeline/build_manifest.py to emit the manifests."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

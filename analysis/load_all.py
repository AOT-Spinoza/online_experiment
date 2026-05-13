"""
Walk a directory tree and assemble a long-format per-trial DataFrame
joined against the private manifest. The output is the canonical
"all subjects × all trials" responses table that every later stage
of the analysis (per_subject, per_video, per_source) aggregates from.

Two input layouts are supported:

  1. JATOS multi-file export (the production path)
     <root>/study_result_X/comp-result_Y/files/<PID>_<suffix>.csv
     Each `comp-result_Y/` directory is one session. Within it, the
     loader prefers `_final.csv` (cumulative superset), or falls back
     to the highest-numbered `_blockN.csv` if the participant
     dropped out before the final-save trial ran.

  2. Single-file dumps (local dev, OSF/Prolific direct, ad-hoc imports)
     <root>/*.json    ← localStorage JSON bundle (dev "Download saved data")
     <root>/*.csv     ← bare CSV
     <root>/*.txt     ← JATOS data.txt (CSV inside, sometimes Excel-mangled)
     Treated as one session per file.

Sessions are uniquely identified by `(pid, session_start_ms)`. If a
PID has multiple sessions (rare — return participants in local dev),
each is kept as a separate session and their pid_session_id distinguishes
them in the output.

Outputs (default under analysis/derived/, gitignored):
  - responses.parquet — one row per (session × trial). Canonical.

Usage:
    # from a notebook
    from analysis.load_all import build_responses_db
    df = build_responses_db()

    # CLI
    python -m analysis.load_all  # uses defaults
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from .load_data import (
    load_session,
    load_private_manifest,
    join_with_private,
)

# Default paths. Override via build_responses_db arguments.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / 'analysis' / 'data'
DEFAULT_PRIVATE_MANIFEST = REPO_ROOT / 'secrets' / 'manifest_private.json'
DEFAULT_DERIVED_DIR = REPO_ROOT / 'analysis' / 'derived'


def short_hash(pid: str) -> str:
    """Stable 48-bit hash of a PROLIFIC_PID. Used as a privacy-safe
    filename stem for per-subject plots. 12 hex chars = 2^48 ≈ 280T
    distinct values, collision-free for any realistic n_subjects."""
    return hashlib.sha256(str(pid).encode()).hexdigest()[:12]


def find_session_files(root: Path) -> list[Path]:
    """Walk `root` and return one file per detected session.

    Detection rules:
      - For a `comp-result_*/files/` directory: prefer `*_final.csv`,
        then fall back to the highest-numbered `*_block*.csv`.
      - For top-level *.json / *.csv / *.txt files: each one is its
        own session.

    Returns absolute Paths. Order is arbitrary; aggregator dedupes by
    (pid, session_start_ms) anyway.
    """
    out: list[Path] = []
    seen_jatos_dirs: set[Path] = set()

    # 1. JATOS multi-file sessions
    for files_dir in root.rglob('comp-result_*/files'):
        if files_dir in seen_jatos_dirs:
            continue
        seen_jatos_dirs.add(files_dir)

        # Final save is canonical when it exists.
        finals = sorted(files_dir.glob('*_final.csv'))
        if finals:
            out.append(finals[0])
            continue

        # Fall back to highest-numbered block file.
        blocks = sorted(files_dir.glob('*_block*.csv'))
        if blocks:
            # Sort by block number embedded in the name. e.g.
            # 'pid_block3.csv' → 3.
            def block_num(p: Path) -> int:
                stem = p.stem  # 'pid_block3'
                try:
                    return int(stem.rsplit('_block', 1)[1])
                except (ValueError, IndexError):
                    return -1
            blocks.sort(key=block_num)
            out.append(blocks[-1])

    # 2. Top-level / non-JATOS files. Avoid double-counting JATOS files
    # by skipping anything inside an already-handled comp-result_*/files dir.
    jatos_files = {p for d in seen_jatos_dirs for p in d.iterdir() if p.is_file()}
    for ext in ('*.json', '*.csv', '*.txt'):
        for p in root.rglob(ext):
            if p in jatos_files:
                continue
            # Skip the JATOS final-csv that we already picked.
            if p.parent.name == 'files' and p.parent.parent.name.startswith('comp-result_'):
                continue
            out.append(p)

    return sorted(set(out))


def _extract_session_meta(df: pd.DataFrame, source_file: Path) -> tuple[str, int | None]:
    """Pull (pid, session_start_ms) from a per-trial DataFrame."""
    pid_values = df['PROLIFIC_PID'].dropna().unique() if 'PROLIFIC_PID' in df.columns else []
    pid = str(pid_values[0]) if len(pid_values) else f'UNKNOWN_{source_file.stem}'

    ts_ms: int | None = None
    if 'session_start_ms' in df.columns:
        s = pd.to_numeric(df['session_start_ms'], errors='coerce').dropna()
        if len(s):
            ts_ms = int(s.iloc[0])
    return pid, ts_ms


def build_responses_db(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    private_manifest: str | Path = DEFAULT_PRIVATE_MANIFEST,
    out_path: str | Path | None = None,
) -> pd.DataFrame:
    """Walk `data_dir`, load every session, join the private manifest,
    concatenate into one long DataFrame, and write a parquet copy.

    Returns the in-memory DataFrame so callers can branch directly into
    later stages without re-reading from disk.
    """
    data_dir = Path(data_dir)
    private_manifest = Path(private_manifest)
    if out_path is None:
        out_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not private_manifest.exists():
        raise FileNotFoundError(
            f"Private manifest not found at {private_manifest}. "
            f"Run `python pipeline/build_manifest.py ...` first.",
        )
    priv = load_private_manifest(private_manifest)

    session_files = find_session_files(data_dir)
    if not session_files:
        raise FileNotFoundError(f"No session files found under {data_dir}.")

    print(f"[load_all] found {len(session_files)} candidate session file(s) under {data_dir}")

    seen_sessions: dict[tuple[str, int | None], Path] = {}
    frames: list[pd.DataFrame] = []
    for f in session_files:
        try:
            df = load_session(f)
        except Exception as e:
            print(f"[load_all] skipping {f}: {e}")
            continue
        pid, ts_ms = _extract_session_meta(df, f)
        key = (pid, ts_ms)
        if key in seen_sessions:
            print(
                f"[load_all] duplicate session ({pid}, start_ms={ts_ms}) — "
                f"keeping first ({seen_sessions[key].relative_to(data_dir)}), "
                f"skipping {f.relative_to(data_dir)}",
            )
            continue
        seen_sessions[key] = f

        # Per-session housekeeping columns. Attached BEFORE the private
        # manifest join so they propagate through.
        df['pid'] = pid
        df['session_start_ms'] = ts_ms if ts_ms is not None else pd.NA
        df['pid_session_id'] = f"{pid}__{ts_ms}" if ts_ms is not None else pid
        df['pid_hash'] = short_hash(pid)
        df['session_source_file'] = str(f)

        joined = join_with_private(df, priv)
        frames.append(joined)

    if not frames:
        raise RuntimeError("No sessions loaded successfully.")

    all_df = pd.concat(frames, axis=0, ignore_index=True)

    # Tidy column ordering: identity → trial location → stimulus →
    # response → derived → QC. Drop columns that are uniformly NaN
    # to keep the parquet lean.
    preferred = [
        'pid', 'pid_hash', 'pid_session_id', 'session_start_ms', 'session_source_file',
        'test_mode',
        'phase', 'block_index', 'trial_index_in_block', 'trial_type_tag',
        'stimulus_id', 'pm_type', 'pm_direction', 'pm_expected_confidence', 'direction_true',
        'response', 'response_direction', 'direction_rt',
        'confidence', 'confidence_rt', 'confidence_correct',
        'correct', 'main_correct', 'catch_passed',
        'play_completed', 'video_play_duration_ms',
        'time_elapsed', 'rt',
        'is_practice_catch_demo', 'save_suffix',
    ]
    cols = [c for c in preferred if c in all_df.columns]
    cols += [c for c in all_df.columns if c not in cols]
    all_df = all_df[cols]

    # Parquet wants consistent dtypes. Coerce a few troublesome ones.
    for c in ('block_index', 'trial_index_in_block', 'session_start_ms', 'time_elapsed'):
        if c in all_df.columns:
            all_df[c] = pd.to_numeric(all_df[c], errors='coerce').astype('Int64')
    for c in ('confidence',):
        if c in all_df.columns:
            all_df[c] = pd.to_numeric(all_df[c], errors='coerce').astype('Int64')
    for c in ('direction_rt', 'confidence_rt', 'rt', 'video_play_duration_ms'):
        if c in all_df.columns:
            all_df[c] = pd.to_numeric(all_df[c], errors='coerce').astype('Float64')

    # Drop session-internal columns that are always NaN for stimulus rows
    # (e.g. 'success', 'timeout' from preload trials) — they bloat the
    # parquet and aren't part of the analysis surface.
    all_df.to_parquet(out_path, index=False)

    print(f"[load_all] wrote {len(all_df):,} rows to {out_path}")
    print(f"[load_all] {len(seen_sessions)} unique sessions across "
          f"{len({k[0] for k in seen_sessions})} distinct pids.")
    return all_df


def _print_summary(df: pd.DataFrame) -> None:
    """Quick stdout summary of what was loaded."""
    print()
    print('=== sessions ===')
    sess = (
        df[['pid', 'pid_hash', 'pid_session_id', 'session_start_ms']]
        .drop_duplicates()
        .copy()
    )
    if 'session_start_ms' in sess.columns:
        sess['session_start_iso'] = pd.to_datetime(
            sess['session_start_ms'], unit='ms', utc=True, errors='coerce',
        ).dt.strftime('%Y-%m-%d %H:%M:%S')
    print(sess.to_string(index=False))
    print()
    print('=== rows by phase × trial_type_tag ===')
    print(df.groupby(['phase', 'trial_type_tag'], dropna=False).size().unstack(fill_value=0))


if __name__ == '__main__':
    df = build_responses_db()
    _print_summary(df)

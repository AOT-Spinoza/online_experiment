"""
Per-source-clip identifiability table.

Pivots per_video into one row per source NNNN, with forward/backward
columns side by side. Useful for spotting *asymmetric* clips —
sources where one direction is far more identifiable than the other,
which is informationally the most interesting case for the
arrow-of-time literature.

Source → direction → stimulus_id mapping comes from the private
manifest's `source_file` column (e.g. '0042_fw.mp4' → source_id='0042',
direction='forward'). For practice/qualification sources the pattern
is `practice/<name>_fw.mp4`; those are excluded from this pivot
because they don't appear in main blocks anyway.

Usage:
    from analysis.per_source import build_per_source_table
    df = build_per_source_table()

    python -m analysis.per_source
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .load_all import DEFAULT_DERIVED_DIR, DEFAULT_PRIVATE_MANIFEST
from .load_data import load_private_manifest


# Main-corpus source files are 'NNNN_fw.mp4' or 'NNNN_rv.mp4'. Catch
# trials are 'catch_fwd_C.mp4' / 'catch_rv_C.mp4'. We only pivot main
# real clips here.
_MAIN_SOURCE_RE = re.compile(r'^(?P<src>\d{4,})_(?P<dir>fw|rv)\.mp4$')


def _parse_source_path(source_file: str | None) -> tuple[str | None, str | None]:
    """Extract (source_id, direction) from a private-manifest source_file.
    Returns (None, None) for catches and practice/qualification clips."""
    if not isinstance(source_file, str):
        return None, None
    base = source_file.rsplit('/', 1)[-1]
    m = _MAIN_SOURCE_RE.match(base)
    if not m:
        return None, None
    src = m.group('src')
    dir_ = 'forward' if m.group('dir') == 'fw' else 'backward'
    return src, dir_


def build_per_source_table(
    per_video_path: str | Path | None = None,
    private_manifest: str | Path = DEFAULT_PRIVATE_MANIFEST,
    out_tsv: str | Path | None = None,
) -> pd.DataFrame:
    if per_video_path is None:
        per_video_path = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    if out_tsv is None:
        out_tsv = DEFAULT_DERIVED_DIR / 'per_source.tsv'

    per_video = pd.read_csv(per_video_path, sep='\t')
    priv = load_private_manifest(private_manifest).reset_index()

    # Attach source_id by joining on stimulus_id, then deriving from source_file.
    parsed = priv.set_index('stimulus_id')['source_file'].apply(_parse_source_path)
    src_df = pd.DataFrame(
        parsed.tolist(), index=parsed.index, columns=['source_id', 'pm_direction_from_path'],
    )
    pv = per_video.merge(
        src_df.reset_index(), on='stimulus_id', how='left',
    )
    pv = pv.dropna(subset=['source_id'])

    # Pivot forward / backward columns.
    keep = [
        'identifiability_score', 'identifiability_score_ci_lo',
        'identifiability_score_ci_hi', 'direction_accuracy_weighted',
        'direction_accuracy_raw', 'n_views', 'mean_direction_rt',
    ]
    fw = pv[pv['pm_direction'] == 'forward'][['source_id'] + keep].rename(
        columns={c: c + '_fw' for c in keep},
    )
    bw = pv[pv['pm_direction'] == 'backward'][['source_id'] + keep].rename(
        columns={c: c + '_bw' for c in keep},
    )
    out = fw.merge(bw, on='source_id', how='outer')

    # Derived columns: asymmetry + mean identifiability.
    if 'identifiability_score_fw' in out.columns and 'identifiability_score_bw' in out.columns:
        out['asymmetry'] = out['identifiability_score_fw'] - out['identifiability_score_bw']
        out['mean_identifiability'] = (
            out[['identifiability_score_fw', 'identifiability_score_bw']].mean(axis=1)
        )
        out['preferred_direction'] = out['asymmetry'].apply(
            lambda x: 'forward' if pd.notna(x) and x > 0
            else ('backward' if pd.notna(x) and x < 0 else 'tied'),
        )

    # Sort: most asymmetric first (high |asymmetry|), then by mean identifiability.
    if 'asymmetry' in out.columns:
        out = out.assign(_abs_asym=out['asymmetry'].abs()) \
                 .sort_values(['_abs_asym', 'mean_identifiability'],
                              ascending=[False, False], na_position='last') \
                 .drop(columns='_abs_asym') \
                 .reset_index(drop=True)

    out.to_csv(out_tsv, sep='\t', index=False, float_format='%.4f')
    print(f"[per_source] wrote {len(out)} source rows to {out_tsv}")
    return out


if __name__ == '__main__':
    build_per_source_table()

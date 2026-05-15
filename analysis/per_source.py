"""
Per-source-clip identifiability table.

Pivots per_video into one row per source NNNN, with forward/backward
columns side by side. Useful for spotting *asymmetric* clips —
sources where one direction is far more identifiable than the other,
which is informationally the most interesting case for the
arrow-of-time literature.

Asymmetry columns, in increasing order of "this is the real signal":

  asymmetry            = id_fw − id_bw. Raw. Contaminated by the
                         population forward-response bias, which
                         inflates id_fw and deflates id_bw on every
                         clip — so the corpus mean is positive for a
                         reason that says nothing about any one clip.
  asymmetry_z          = arctanh(id_fw) − arctanh(id_bw). The arctanh
                         (= Fisher-z = ½·logit) transform decompresses
                         the bounded [-1,+1] scale so a near-ceiling
                         shift counts as much as a mid-range one.
  asymmetry_residual   = asymmetry − median(asymmetry). Raw scale with
                         the forward-bias offset removed.
  asymmetry_z_residual = asymmetry_z − median(asymmetry_z). Both
                         corrections applied — THIS is the per-clip
                         signal: how the source's directionality
                         deviates from the corpus-wide bias baseline.
                         > 0 → forward render carries the cleaner cue;
                         < 0 → reversed render is the more conspicuous
                         one (e.g. a salient anti-gravity / anti-
                         entropy event).

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

import numpy as np
import pandas as pd

from .load_all import DEFAULT_DERIVED_DIR, DEFAULT_PRIVATE_MANIFEST
from .load_data import load_private_manifest

# Identifiability scores are bounded in [-1, +1]; arctanh(±1) = ±inf, so
# we clip just inside the bounds before transforming. arctanh(0.99) ≈ 2.6.
_Z_CLIP = 0.99


def _arctanh_clip(s: pd.Series) -> pd.Series:
    """arctanh (= Fisher-z = ½·logit) of an identifiability score, with the
    score clipped to ±_Z_CLIP first so an exact ±1 doesn't map to ±infinity.

    This decompresses the bounded [-1,+1] scale: a shift near the ceiling
    counts as a larger change in latent evidence than the same nominal
    shift mid-range. Applied per direction so the difference of the two
    transformed scores is a compression-corrected asymmetry."""
    return np.arctanh(np.clip(s.astype(float), -_Z_CLIP, _Z_CLIP))


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

    # Derived columns: asymmetry, its compression-corrected and
    # bias-removed variants, and mean identifiability.
    if 'identifiability_score_fw' in out.columns and 'identifiability_score_bw' in out.columns:
        fw_id = out['identifiability_score_fw']
        bw_id = out['identifiability_score_bw']

        # (1) Raw asymmetry — kept for comparison / transparency.
        out['asymmetry'] = fw_id - bw_id
        out['mean_identifiability'] = (
            out[['identifiability_score_fw', 'identifiability_score_bw']].mean(axis=1)
        )

        # (2) Compression-corrected asymmetry. arctanh EACH score, THEN
        # difference — so a near-ceiling shift isn't mechanically squashed
        # relative to a mid-range shift.
        out['identifiability_fw_z'] = _arctanh_clip(fw_id)
        out['identifiability_bw_z'] = _arctanh_clip(bw_id)
        out['asymmetry_z'] = out['identifiability_fw_z'] - out['identifiability_bw_z']

        # (3) Bias-removed residuals. The population forward-response bias
        # inflates id_fw and deflates id_bw on EVERY clip, so it adds a
        # ~constant positive offset to every source's asymmetry. We
        # estimate that offset as the MEDIAN asymmetry over well-sampled
        # sources — the median is robust to the genuinely-asymmetric
        # clips (which are the per-clip signal we don't want polluting
        # the offset estimate). The residual is the per-clip signal:
        # how this source deviates from the corpus-wide bias baseline.
        both = fw_id.notna() & bw_id.notna()
        well = both
        if 'n_views_fw' in out.columns and 'n_views_bw' in out.columns:
            well = both & (out['n_views_fw'] >= 3) & (out['n_views_bw'] >= 3)
        offset_raw = float(out.loc[well, 'asymmetry'].median()) if well.any() else 0.0
        offset_z = float(out.loc[well, 'asymmetry_z'].median()) if well.any() else 0.0
        out['asymmetry_residual'] = out['asymmetry'] - offset_raw
        out['asymmetry_z_residual'] = out['asymmetry_z'] - offset_z

        # preferred_direction now reflects the BIAS-ADJUSTED residual —
        # "which direction reads more cleanly once the global forward
        # bias is removed", not the raw (bias-contaminated) asymmetry.
        def _pref(v):
            if pd.isna(v):
                return 'n/a'
            if v > 0.05:
                return 'forward'
            if v < -0.05:
                return 'backward'
            return 'balanced'
        out['preferred_direction'] = out['asymmetry_z_residual'].apply(_pref)

        print(
            f"[per_source] corpus forward-bias offset estimated from "
            f"{int(well.sum())} well-sampled sources: "
            f"median raw asymmetry = {offset_raw:+.3f}, z-scale = {offset_z:+.3f}"
        )

    # Sort: strongest bias-adjusted asymmetry first (the per-clip signal).
    if 'asymmetry_z_residual' in out.columns:
        out = out.assign(_abs=out['asymmetry_z_residual'].abs()) \
                 .sort_values(['_abs', 'mean_identifiability'],
                              ascending=[False, False], na_position='last') \
                 .drop(columns='_abs') \
                 .reset_index(drop=True)
    elif 'asymmetry' in out.columns:
        out = out.assign(_abs=out['asymmetry'].abs()) \
                 .sort_values('_abs', ascending=False, na_position='last') \
                 .drop(columns='_abs') \
                 .reset_index(drop=True)

    out.to_csv(out_tsv, sep='\t', index=False, float_format='%.4f')
    print(f"[per_source] wrote {len(out)} source rows to {out_tsv}")
    return out


if __name__ == '__main__':
    build_per_source_table()

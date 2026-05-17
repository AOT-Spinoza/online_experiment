"""
Stability / convergence analysis for the per-video identifiability score.

The question: as more participants watch a given clip, does its
identifiability estimate stop moving — and how many viewers do we
actually need before the estimate (and the clip ranking it feeds) is
trustworthy?

We answer it by *subsampling* the well-sampled cells (a "cell" = one
stimulus_id × direction). For a cell that already has N viewings we can
ask what a k-viewer estimate would have looked like, for k swept from
small up to N, and measure the spread. Two complementary statistics:

  1. PRECISION  — m-out-of-n bootstrap. For each k, resample k viewings
     (with replacement) B times and recompute the identifiability
     score; the SD across the B resamples is the precision of a
     k-viewer estimate, sigma(k). Also reports the bootstrap bias
     (mean resampled score − full-cell score). Answers: "a k-viewer
     identifiability score has SD ≈ sigma(k)."

  2. SPLIT-HALF RELIABILITY — for each k, randomly partition a cell's
     viewings into two DISJOINT halves of size k, score each half, and
     correlate the two half-scores across all cells. r(k) is the
     reliability of a k-viewer measurement; r → 1 means the estimate
     has converged. Because the two halves share no data, there is no
     "the subsample is part of the reference" optimism — this is a
     clean Spearman-Brown-style reliability curve.

Both are computed separately for forward and backward renders, because
the backward (reversed) clips have a much wider identifiability
distribution and may need more viewers to stabilise.

Inputs : analysis/derived/responses.parquet  (per-trial data)
         analysis/derived/per_subject.tsv     (subject_quality_weight —
                                               the identifiability score
                                               is quality-weighted)
Output : analysis/derived/stability.tsv

Usage:
    from analysis.stability import build_stability_table
    build_stability_table()

    python -m analysis.stability
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .load_all import DEFAULT_DERIVED_DIR

# --- analysis parameters ---------------------------------------------------
# Only cells with at least this many viewings are used as the "population"
# we subsample from — the empirical distribution needs to be reasonably
# well estimated for the bootstrap to be meaningful.
N_FLOOR = 20
# Viewer counts swept for the precision (m-out-of-n bootstrap) curve.
PRECISION_K = (2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 21, 25, 30)
# Viewer counts for the split-half curve (each cell needs N >= 2k).
SPLITHALF_K = (2, 3, 4, 5, 6, 8, 10, 12)
B_BOOTSTRAP = 400          # resamples per cell per k (precision)
R_SPLITHALF = 200          # random partitions per k (split-half)
MAX_CONF = 5               # confidence scale ceiling (identifiability scaling)
_SEED = 20260517


def _load_cells(responses_path: Path, per_subject_path: Path) -> dict:
    """Return {(stimulus_id, direction): (signed_conf, weight)} arrays for
    main-task real trials, with subject quality weights joined in.

    `signed_conf = (2·correct − 1)·confidence` is the per-trial bettor
    signal — precomputing it means the bootstrap inner loop is a pure
    weighted-sum, identical to per_video._id_score."""
    df = pd.read_parquet(responses_path)
    stim = df[
        (df['trial_type_tag'] == 'stimulus')
        & (df['phase'] == 'main')
        & (df['pm_type'] == 'main')
    ].copy()
    stim['confidence'] = pd.to_numeric(stim['confidence'], errors='coerce')
    stim['main_correct'] = stim['main_correct'].astype('boolean')
    stim = stim.dropna(subset=['main_correct', 'confidence', 'response_direction'])

    ps = pd.read_csv(per_subject_path, sep='\t')[
        ['pid_session_id', 'subject_quality_weight']
    ]
    stim = stim.merge(ps, on='pid_session_id', how='left')
    stim['subject_quality_weight'] = stim['subject_quality_weight'].fillna(0.0)

    cells: dict = {}
    for (sid, direction), g in stim.groupby(['stimulus_id', 'pm_direction']):
        c = g['main_correct'].astype(int).to_numpy()
        conf = g['confidence'].to_numpy(dtype=float)
        w = g['subject_quality_weight'].to_numpy(dtype=float)
        signed = (2 * c - 1) * conf            # per-trial signed confidence
        cells[(str(sid), str(direction))] = (signed, w)
    return cells


def _score(signed: np.ndarray, w: np.ndarray) -> float:
    """Identifiability score for one (sub)sample — matches per_video._id_score."""
    sw = w.sum()
    if sw <= 0:
        return float('nan')
    return float((signed * w).sum() / (MAX_CONF * sw))


def _precision_rows(cells: dict, rng: np.random.Generator) -> list[dict]:
    """m-out-of-n bootstrap precision curve, per direction × k."""
    # Per (direction, k): collect each cell's bootstrap SD and bias.
    acc: dict[tuple, list[tuple[float, float]]] = {}
    for (sid, direction), (signed, w) in cells.items():
        n = len(signed)
        if n < N_FLOOR:
            continue
        full = _score(signed, w)
        for k in PRECISION_K:
            if k > n:                       # never resample more than the cell has
                continue
            # Vectorised over the B resamples: (B, k) index matrix.
            idx = rng.integers(0, n, size=(B_BOOTSTRAP, k))
            s = signed[idx]                 # (B, k)
            ww = w[idx]                     # (B, k)
            sw = ww.sum(axis=1)             # (B,)
            with np.errstate(invalid='ignore', divide='ignore'):
                sc = np.where(sw > 0, (s * ww).sum(axis=1) / (MAX_CONF * sw), np.nan)
            sd = float(np.nanstd(sc))
            bias = float(np.nanmean(sc) - full) if np.isfinite(full) else np.nan
            acc.setdefault((direction, k), []).append((sd, bias))

    rows = []
    for (direction, k), vals in sorted(acc.items()):
        sds = np.array([v[0] for v in vals], dtype=float)
        biases = np.array([v[1] for v in vals], dtype=float)
        rows.append({
            'analysis': 'precision',
            'direction': direction,
            'k': k,
            'n_cells': len(vals),
            'sd_median': float(np.nanmedian(sds)),
            'sd_q25': float(np.nanpercentile(sds, 25)),
            'sd_q75': float(np.nanpercentile(sds, 75)),
            'bias_median': float(np.nanmedian(biases)),
            'splithalf_spearman': np.nan,
            'splithalf_pearson': np.nan,
        })
    return rows


def _splithalf_rows(cells: dict, rng: np.random.Generator) -> list[dict]:
    """Disjoint split-half reliability curve, per direction × k."""
    # Group eligible cells by direction.
    by_dir: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for (sid, direction), (signed, w) in cells.items():
        if len(signed) >= N_FLOOR:
            by_dir.setdefault(direction, []).append((signed, w))

    rows = []
    for direction, cell_list in sorted(by_dir.items()):
        for k in SPLITHALF_K:
            eligible = [(s, w) for (s, w) in cell_list if len(s) >= 2 * k]
            if len(eligible) < 30:          # too few cells for a stable r
                continue
            # Sa_mat / Sb_mat: (R, n_cells) — half-scores across cells.
            Sa = np.full((R_SPLITHALF, len(eligible)), np.nan)
            Sb = np.full((R_SPLITHALF, len(eligible)), np.nan)
            for ci, (signed, w) in enumerate(eligible):
                n = len(signed)
                # R permutations at once; first k vs next k = disjoint halves.
                perms = np.argsort(rng.random((R_SPLITHALF, n)), axis=1)
                ia = perms[:, :k]
                ib = perms[:, k:2 * k]
                for half, store in ((ia, Sa), (ib, Sb)):
                    s = signed[half]
                    ww = w[half]
                    sw = ww.sum(axis=1)
                    with np.errstate(invalid='ignore', divide='ignore'):
                        store[:, ci] = np.where(
                            sw > 0, (s * ww).sum(axis=1) / (MAX_CONF * sw), np.nan,
                        )
            # Across-cell correlation per repetition, then average.
            sp, pe = [], []
            for r in range(R_SPLITHALF):
                a, b = Sa[r], Sb[r]
                m = np.isfinite(a) & np.isfinite(b)
                if m.sum() < 10:
                    continue
                sp.append(spearmanr(a[m], b[m]).statistic)
                pe.append(float(np.corrcoef(a[m], b[m])[0, 1]))
            if not sp:
                continue
            rows.append({
                'analysis': 'splithalf',
                'direction': direction,
                'k': k,
                'n_cells': len(eligible),
                'sd_median': np.nan,
                'sd_q25': np.nan,
                'sd_q75': np.nan,
                'bias_median': np.nan,
                'splithalf_spearman': float(np.mean(sp)),
                'splithalf_pearson': float(np.mean(pe)),
            })
    return rows


def build_stability_table(
    responses_path: str | Path | None = None,
    per_subject_path: str | Path | None = None,
    out_tsv: str | Path | None = None,
) -> pd.DataFrame:
    """Run the precision + split-half subsampling analysis and write
    analysis/derived/stability.tsv. Returns the table."""
    if responses_path is None:
        responses_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    if out_tsv is None:
        out_tsv = DEFAULT_DERIVED_DIR / 'stability.tsv'
    out_tsv = Path(out_tsv)

    cells = _load_cells(Path(responses_path), Path(per_subject_path))
    n_well = sum(1 for (s, _w) in cells.values() if len(s) >= N_FLOOR)
    print(f"[stability] {len(cells):,} cells loaded; "
          f"{n_well:,} with >= {N_FLOOR} views (the subsampling pool)")
    if n_well < 50:
        print(f"[stability] WARNING: only {n_well} well-sampled cells — "
              f"the curves will be noisy until more data is collected.")

    rng = np.random.default_rng(_SEED)
    rows = _precision_rows(cells, rng)
    rows += _splithalf_rows(cells, rng)
    out = pd.DataFrame(rows)
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_tsv, sep='\t', index=False, float_format='%.4f')
    print(f"[stability] wrote {len(out)} rows to {out_tsv}")
    _print_summary(out)
    return out


def spearman_brown(r_k: float, k: int, target_k: int) -> float:
    """Spearman-Brown prophecy: project the reliability of a k-viewer
    measurement (r_k) to a target_k-viewer measurement. Used to reach
    past the directly-measurable split-half range (the split-half needs
    N >= 2k, so it caps around k = 12-14 with current view counts)."""
    if not np.isfinite(r_k) or r_k <= 0:
        return float('nan')
    m = target_k / k
    return float((m * r_k) / (1.0 + (m - 1.0) * r_k))


def k_for_reliability(r_k: float, k: int, target_r: float = 0.90) -> float:
    """Invert Spearman-Brown: how many viewers are needed to reach
    `target_r`, given a measured reliability r_k at k viewers."""
    if not np.isfinite(r_k) or r_k <= 0 or r_k >= 1:
        return float('nan')
    # target_r = m·r / (1 + (m−1)·r)  →  m = target_r·(1−r) / (r·(1−target_r))
    m = (target_r * (1.0 - r_k)) / (r_k * (1.0 - target_r))
    return float(m * k)


def _print_summary(df: pd.DataFrame) -> None:
    """Stdout summary: precision, measured reliability, and a
    Spearman-Brown projection of the k needed for reliability >= 0.9."""
    prec = df[df['analysis'] == 'precision']
    sh = df[df['analysis'] == 'splithalf']
    print()
    print("=== identifiability-score stability ===")
    for direction in ('forward', 'backward'):
        p = prec[prec['direction'] == direction]
        s = sh[sh['direction'] == direction].sort_values('k')
        print(f"  {direction}:")
        for k in (5, 10, 15, 20):
            pr = p[p['k'] == k]
            sr = s[s['k'] == k]
            sd = f"{pr['sd_median'].iloc[0]:.3f}" if len(pr) else "  -  "
            rel = f"{sr['splithalf_spearman'].iloc[0]:.3f}" if len(sr) else "  -  "
            print(f"    k={k:2d}:  bootstrap SD = {sd}   split-half r = {rel}")
        # Spearman-Brown projection from the largest directly-measured k.
        if len(s):
            k_meas = int(s['k'].iloc[-1])
            r_meas = float(s['splithalf_spearman'].iloc[-1])
            r20 = spearman_brown(r_meas, k_meas, 20)
            k90 = k_for_reliability(r_meas, k_meas, 0.90)
            print(f"    Spearman-Brown (from measured r={r_meas:.3f} at k={k_meas}): "
                  f"projected r at k=20 ~ {r20:.3f};  "
                  f"r>=0.90 needs k ~ {k90:.0f} viewers")


if __name__ == '__main__':
    build_stability_table()

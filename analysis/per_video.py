"""
Per-video (per-(stimulus_id, direction)) identifiability table.

Reads responses.parquet + per_subject.tsv, joins each main-task trial
to its subject's quality weight, then aggregates per (stimulus_id,
pm_direction) into:

  Confidence-weighted "bettor" score (THE headline metric)
    Per trial:    signed_conf = (2·main_correct − 1) · confidence    [-5..+5]
    Per video:    identifiability_score
                  = Σ (signed_conf_i · w_i) / (5 · Σ w_i)             [-1..+1]
    +1 = everyone identifies it confidently and correctly
     0 = chance / split crowd / everyone guesses
    −1 = systematic misidentification (everyone confidently wrong)

  Raw accuracy (companion — no confidence weighting)
    direction_accuracy_raw       = mean(main_correct)
    direction_accuracy_weighted  = subject-quality-weighted mean(main_correct)

  Bootstrap CIs (default 1000 resamples)
    identifiability_score_ci_lo / _ci_hi
    direction_accuracy_weighted_ci_lo / _ci_hi

  Dispersion (distinguishes "split crowd" from "everyone guesses")
    confidence_dispersion = std(confidence)
    direction_dispersion  = std(2·correct − 1)

  Stratified means
    mean_confidence_correct, mean_confidence_wrong
    mean_direction_rt

  Ranking
    identifiability_decile  ← 0..9 bucket on identifiability_score

Usage:
    from analysis.per_video import build_per_video_table
    df = build_per_video_table()

    python -m analysis.per_video
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .load_all import DEFAULT_DERIVED_DIR


def _signed_confidence(correct: np.ndarray, confidence: np.ndarray) -> np.ndarray:
    """Per-trial bettor signal: + if correct, − if wrong; magnitude = confidence.
    Operates element-wise on already-cleaned (non-NaN) arrays."""
    return (2 * correct.astype(int) - 1) * confidence.astype(float)


def _id_score(signed_conf: np.ndarray, weights: np.ndarray, max_conf: int = 5) -> float:
    """Identifiability = weighted mean of signed confidence, scaled to [-1, +1]."""
    if weights.sum() <= 0:
        return float('nan')
    return float(np.sum(signed_conf * weights) / (max_conf * np.sum(weights)))


def _weighted_acc(correct: np.ndarray, weights: np.ndarray) -> float:
    if weights.sum() <= 0:
        return float('nan')
    return float(np.sum(correct.astype(int) * weights) / np.sum(weights))


def _bootstrap_ci(
    correct: np.ndarray,
    confidence: np.ndarray,
    weights: np.ndarray,
    n_resamples: int,
    rng: np.random.Generator,
    metric: str,
) -> tuple[float, float]:
    """Percentile bootstrap. metric ∈ {'id_score', 'weighted_acc'}.
    Returns (ci_lo, ci_hi) at the 2.5/97.5 percentiles."""
    n = len(correct)
    if n < 2 or n_resamples <= 0:
        return float('nan'), float('nan')
    samples = np.empty(n_resamples, dtype=np.float64)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        c, conf, w = correct[idx], confidence[idx], weights[idx]
        if metric == 'id_score':
            samples[i] = _id_score(_signed_confidence(c, conf), w)
        else:
            samples[i] = _weighted_acc(c, w)
    return float(np.nanpercentile(samples, 2.5)), float(np.nanpercentile(samples, 97.5))


def build_per_video_table(
    responses_path: str | Path | None = None,
    per_subject_path: str | Path | None = None,
    out_tsv: str | Path | None = None,
    n_bootstrap: int = 1000,
    seed: int = 20260513,
) -> pd.DataFrame:
    if responses_path is None:
        responses_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    if out_tsv is None:
        out_tsv = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    responses_path = Path(responses_path)
    per_subject_path = Path(per_subject_path)
    out_tsv = Path(out_tsv)

    df = pd.read_parquet(responses_path)
    ps = pd.read_csv(per_subject_path, sep='\t')
    print(f"[per_video] loaded {len(df):,} rows, {len(ps)} subject session(s)")

    # Restrict to canonical stimulus rows on the main task's real clips.
    stim = df[df['trial_type_tag'] == 'stimulus'].copy()
    main_real = stim[(stim['phase'] == 'main') & (stim['pm_type'] == 'main')].copy()

    # Coerce dtypes for arithmetic.
    main_real['confidence'] = pd.to_numeric(main_real['confidence'], errors='coerce')
    main_real['direction_rt'] = pd.to_numeric(main_real['direction_rt'], errors='coerce')
    main_real['main_correct'] = main_real['main_correct'].astype('boolean')

    # Drop trials without a correct/confidence/direction response (no signal).
    main_real = main_real.dropna(subset=['main_correct', 'confidence', 'response_direction'])

    # Join in subject quality weight. Rows for excluded subjects keep the
    # raw weight (we don't drop them — analyst can re-filter), but
    # confidence-weighted score will down-weight them automatically.
    qcols = ['pid_session_id', 'subject_quality_weight', 'included']
    main_real = main_real.merge(
        ps[qcols], on='pid_session_id', how='left',
    )
    main_real['subject_quality_weight'] = main_real['subject_quality_weight'].fillna(0.0)

    rng = np.random.default_rng(seed)
    rows = []
    grouped = main_real.groupby(['stimulus_id', 'pm_direction'], dropna=False)
    print(f"[per_video] aggregating {len(grouped)} (stimulus_id × direction) cells")
    for (stim_id, direction), g in grouped:
        c = g['main_correct'].astype(int).to_numpy()
        conf = g['confidence'].to_numpy()
        w = g['subject_quality_weight'].to_numpy()
        signed = _signed_confidence(c, conf)

        id_score = _id_score(signed, w)
        acc_w = _weighted_acc(c, w)
        acc_raw = float(c.mean())
        n_views = len(c)

        # Stratified means
        if c.sum() > 0:
            mean_conf_correct = float(conf[c == 1].mean())
        else:
            mean_conf_correct = float('nan')
        if (1 - c).sum() > 0:
            mean_conf_wrong = float(conf[c == 0].mean())
        else:
            mean_conf_wrong = float('nan')

        # Dispersion
        conf_disp = float(conf.std(ddof=0)) if n_views >= 2 else float('nan')
        dir_disp = float((2 * c - 1).std(ddof=0)) if n_views >= 2 else float('nan')

        # Bootstrap
        id_lo, id_hi = _bootstrap_ci(c, conf, w, n_bootstrap, rng, 'id_score')
        acc_lo, acc_hi = _bootstrap_ci(c, conf, w, n_bootstrap, rng, 'weighted_acc')

        mean_rt = float(g['direction_rt'].mean()) if g['direction_rt'].notna().any() else float('nan')
        n_quality_weighted = float(w.sum())

        rows.append({
            'stimulus_id': stim_id,
            'pm_direction': direction,
            'n_views': n_views,
            'n_quality_weighted': n_quality_weighted,
            'identifiability_score': id_score,
            'identifiability_score_ci_lo': id_lo,
            'identifiability_score_ci_hi': id_hi,
            'direction_accuracy_weighted': acc_w,
            'direction_accuracy_weighted_ci_lo': acc_lo,
            'direction_accuracy_weighted_ci_hi': acc_hi,
            'direction_accuracy_raw': acc_raw,
            'mean_confidence_correct': mean_conf_correct,
            'mean_confidence_wrong': mean_conf_wrong,
            'confidence_dispersion': conf_disp,
            'direction_dispersion': dir_disp,
            'mean_direction_rt': mean_rt,
        })

    out = pd.DataFrame(rows)
    if len(out):
        # Decile ranking on the headline score. NaN scores → NaN decile.
        out['identifiability_decile'] = pd.qcut(
            out['identifiability_score'], 10, labels=False, duplicates='drop',
        ).astype('Int64')
        # Sort: easiest (high score) first.
        out = out.sort_values('identifiability_score', ascending=False, na_position='last')
        out = out.reset_index(drop=True)

    out.to_csv(out_tsv, sep='\t', index=False, float_format='%.4f')
    print(f"[per_video] wrote {len(out)} (stim × direction) rows to {out_tsv}")
    return out


if __name__ == '__main__':
    build_per_video_table()

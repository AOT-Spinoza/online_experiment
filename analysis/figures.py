"""
Publication-quality figures for the Arrow-of-Time experiment.

Where the interactive dashboard (analysis/dashboard.py) is Chart.js/HTML
for exploration, this module produces static *matplotlib* figures saved
as *vector PDFs* - for papers, posters, and slides. Each function reads
the derived TSVs/parquet the analysis pipeline writes under
analysis/derived/, returns a matplotlib Figure, and (if `save_path` is
given) writes a PDF.

The figure set mirrors the dashboard's main panels:
  per_source_asymmetry_figure   forward vs backward identifiability
  cohort_quality_figure         d' / M-ratio / catch / forward-bias
  calibration_figure            accuracy vs reported confidence
  accuracy_drift_figure         rolling accuracy across main trials
  identifiability_figure        per-video identifiability distribution
  confidence_accuracy_figure    the "fooler" scatter (conf vs accuracy)
  corpus_coverage_figure        views-per-cell coverage staircase
  inclusion_figure              inclusion / exclusion-reason breakdown

Driven by analysis/figures.ipynb. To regenerate every figure at once:

    python -m analysis.figures

Output PDFs land in analysis/derived/figures/pub/ (gitignored - they're
regeneratable artifacts; the code that makes them is version-controlled).

Style: Helvetica throughout (text is kept ASCII-only because the macOS
Helvetica glyph set lacks arrows / math symbols); seaborn `despine` with
an offset gives the axes a 'torn-away' look.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.colors import LinearSegmentedColormap, Normalize

from .load_all import DEFAULT_DERIVED_DIR

PUB_DIR = DEFAULT_DERIVED_DIR / 'figures' / 'pub'

# Diverging colormap matching the dashboard's asymmetry scatter:
# blue = reverse-render salient, grey = unremarkable, orange = forward
# render salient.
ASYMMETRY_CMAP = LinearSegmentedColormap.from_list(
    'aot_asymmetry', ['#2a6ea8', '#c9cdd1', '#c8702f'],
)

# Shared palette (consistent with the dashboard).
C_BLUE = '#2a6e8c'
C_ORANGE = '#c08040'
C_INCLUDED = '#2a8c2a'
C_EXCLUDED = '#c43b3b'
C_MEAN = '#1a1a1a'
C_FAINT = '#9aa1a8'

# Shared publication style - Helvetica only. Applied per-figure via a
# local rc_context so importing the module doesn't mutate a notebook's
# global matplotlib state.
_PUB_RC = {
    'font.family': 'Helvetica',
    'font.size': 10,
    'axes.titlesize': 12,
    'axes.labelsize': 10.5,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 8.5,
    'axes.linewidth': 0.8,
    'figure.dpi': 120,            # on-screen; PDF is vector regardless
    'savefig.bbox': 'tight',
    'pdf.fonttype': 42,           # embed TrueType so text stays editable
}


def _styled():
    """Context manager applying the publication rcParams locally."""
    return plt.rc_context(_PUB_RC)


def _despine(ax, offset: int = 8):
    """Seaborn despine with an offset: drop the top + right spines and
    push the remaining left + bottom spines `offset` points away from
    the data, giving the axes a 'torn-away' look. Call after the
    artists are drawn."""
    sns.despine(ax=ax, offset=offset, trim=False)


def _save(fig, save_path) -> None:
    """Write `fig` to a vector PDF (creating parent dirs as needed)."""
    if save_path is None:
        return
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, format='pdf')
    print(f"[figures] wrote {save_path}")


def _prolific(ps: pd.DataFrame, included_only: bool = False) -> pd.DataFrame:
    """Prolific sessions only (drop LOCAL_ dev runs); optionally restrict
    to the inclusion-gated cohort."""
    out = ps[~ps['pid'].astype(str).str.startswith('LOCAL_')].copy()
    if included_only and 'included' in out.columns:
        out = out[out['included'] == True]  # noqa: E712
    return out


# ---------------------------------------------------------------------------
# Figure - per-source arrow-of-time asymmetry
# ---------------------------------------------------------------------------

def per_source_asymmetry_figure(
    per_source_path: str | Path | None = None,
    save_path: str | Path | None = None,
    min_views: int = 3,
    annotate_top: int = 8,
    figsize: tuple[float, float] = (7.0, 6.2),
):
    """Forward-render vs backward-render identifiability, one point per
    source video, coloured by the bias-adjusted asymmetry residual.

      - solid diagonal  y = x          raw symmetry
      - dashed diagonal y = x - offset bias-adjusted symmetry (the
        forward-bias offset; gap between the diagonals)
      - point colour    asymmetry_z_residual - arctanh-decompressed,
        forward-bias removed. Orange = forward render carries the
        cleaner cue; blue = the reversed render is the conspicuous one.
      - point size      min(n_views_fw, n_views_bw)
    """
    if per_source_path is None:
        per_source_path = DEFAULT_DERIVED_DIR / 'per_source.tsv'
    df = pd.read_csv(per_source_path, sep='\t')

    needed = ['identifiability_score_fw', 'identifiability_score_bw',
              'asymmetry_z_residual']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(
            f"per_source.tsv is missing {missing} - rerun "
            f"`python -m analysis.per_source` with the current code.",
        )
    df = df.dropna(subset=needed).copy()
    if min_views and {'n_views_fw', 'n_views_bw'}.issubset(df.columns):
        df = df[(df['n_views_fw'] >= min_views) & (df['n_views_bw'] >= min_views)]
    if not len(df):
        raise ValueError("no sources left to plot after the min_views filter")

    offset_raw = 0.0
    if {'asymmetry', 'asymmetry_residual'}.issubset(df.columns):
        d = (df['asymmetry'] - df['asymmetry_residual']).dropna()
        if len(d):
            offset_raw = float(d.median())

    x = df['identifiability_score_fw'].to_numpy()
    y = df['identifiability_score_bw'].to_numpy()
    resid = df['asymmetry_z_residual'].to_numpy()

    vmax = float(np.nanpercentile(np.abs(resid), 97)) or 1.0
    norm = Normalize(vmin=-vmax, vmax=vmax)

    if {'n_views_fw', 'n_views_bw'}.issubset(df.columns):
        nmin = np.minimum(df['n_views_fw'], df['n_views_bw']).to_numpy()
    else:
        nmin = np.full(len(df), 4)
    sizes = 10.0 + 6.0 * np.log2(np.maximum(2, nmin))

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

        lim = (-1.05, 1.05)
        ax.axhline(0, color='#e6e6e6', lw=0.7, zorder=0)
        ax.axvline(0, color='#e6e6e6', lw=0.7, zorder=0)
        ax.plot(lim, lim, color='#b5b5b5', lw=1.0, zorder=1,
                label='raw symmetry  (y = x)')
        ax.plot(lim, [v - offset_raw for v in lim], color='#444444',
                lw=1.3, ls=(0, (5, 3)), zorder=1,
                label=f'bias-adjusted symmetry  (y = x - {offset_raw:.2f})')

        sc = ax.scatter(
            x, y, c=resid, cmap=ASYMMETRY_CMAP, norm=norm, s=sizes,
            edgecolors='#2c2c2c', linewidths=0.3, alpha=0.9, zorder=3,
        )

        if annotate_top:
            _collide = 0.13
            placed: list[tuple[float, float]] = []
            for i in np.argsort(-np.abs(resid)):
                if len(placed) >= annotate_top:
                    break
                xi, yi = float(x[i]), float(y[i])
                if any((xi - px) ** 2 + (yi - py) ** 2 < _collide ** 2
                       for px, py in placed):
                    continue
                placed.append((xi, yi))
                dx = -1 if xi > 0.35 else 1
                dy = -1 if yi > 0.35 else 1
                ax.annotate(
                    str(df.iloc[i]['source_id']),
                    (xi, yi),
                    textcoords='offset points',
                    xytext=(15 * dx, 13 * dy),
                    fontsize=7, color='#1a1a1a', zorder=5,
                    ha='right' if dx < 0 else 'left',
                    va='top' if dy < 0 else 'bottom',
                    arrowprops=dict(arrowstyle='-', color='#888888',
                                    lw=0.5, shrinkA=0, shrinkB=2),
                )

        cbar = fig.colorbar(sc, ax=ax, shrink=0.86, pad=0.02)
        cbar.set_label(
            'bias-adjusted asymmetry  (arctanh z-residual)\n'
            'negative: reversed render more conspicuous   /   '
            'positive: forward render carries cleaner cue',
            fontsize=8.5,
        )
        cbar.outline.set_linewidth(0.6)

        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_aspect('equal', adjustable='box')
        ax.set_xlabel('forward-render identifiability')
        ax.set_ylabel('backward-render identifiability')
        ax.set_title('Per-source arrow-of-time asymmetry', pad=10)
        ax.legend(loc='upper left', frameon=False, handlelength=2.4)
        ax.text(
            -1.0, -0.60,
            f'n = {len(df):,} sources  (>= {min_views} views / direction)\n'
            f'forward-bias offset = +{offset_raw:.2f}  (gap between diagonals)',
            fontsize=7.5, color='#555555', ha='left', va='top',
            bbox=dict(boxstyle='round,pad=0.35', fc='white',
                      ec='#dddddd', lw=0.6),
        )
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - cohort quality (d' / M-ratio / catch / forward bias)
# ---------------------------------------------------------------------------

def cohort_quality_figure(
    per_subject_path: str | Path | None = None,
    save_path: str | Path | None = None,
    included_only: bool = True,
    figsize: tuple[float, float] = (9.0, 7.4),
):
    """2x2 grid of cohort-quality distributions: type-1 sensitivity d',
    metacognitive efficiency M-ratio, catch-direction pass rate, and
    forward bias. Dashed red = cohort median; dotted dark = a reference
    value (ideal M-ratio = 1, catch gate = 0.80, zero bias)."""
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    ps = _prolific(pd.read_csv(per_subject_path, sep='\t'), included_only=included_only)
    n = len(ps)

    panels = [
        ('d_prime', "type-1 sensitivity  d'", None, C_BLUE),
        ('m_ratio', 'metacognitive efficiency  M-ratio', 1.0, C_ORANGE),
        ('catch_direction_pass_rate', 'catch-direction pass rate', 0.80, C_BLUE),
        ('forward_bias', 'forward bias  (P(say forward) - 0.5)', 0.0, C_ORANGE),
    ]
    with _styled():
        fig, axes = plt.subplots(2, 2, figsize=figsize, constrained_layout=True)
        for ax, (col, label, ref, color) in zip(axes.flat, panels):
            v = pd.to_numeric(ps[col], errors='coerce').dropna()
            ax.hist(v, bins=18, color=color, alpha=0.85,
                    edgecolor='white', linewidth=0.5)
            if len(v):
                med = float(v.median())
                ax.axvline(med, color=C_EXCLUDED, lw=1.6, ls='--',
                           label=f'median = {med:.2f}')
            if ref is not None:
                ax.axvline(ref, color='#444444', lw=1.0, ls=':',
                           label=f'reference = {ref:g}')
            ax.set_xlabel(label)
            ax.set_ylabel('subjects')
            ax.legend(frameon=False, fontsize=8)
            _despine(ax)
        cohort = 'included' if included_only else 'all'
        fig.suptitle(f'Cohort quality  -  {n} {cohort} Prolific subjects',
                     fontsize=13)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - confidence calibration
# ---------------------------------------------------------------------------

def calibration_figure(
    per_subject_path: str | Path | None = None,
    save_path: str | Path | None = None,
    included_only: bool = True,
    figsize: tuple[float, float] = (6.4, 5.4),
):
    """Direction accuracy at each reported confidence level (1-5). One
    faint line per subject; bold black = cohort mean with a shaded
    inter-quartile band; dashed red = chance."""
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    ps = _prolific(pd.read_csv(per_subject_path, sep='\t'), included_only=included_only)

    conf_cols = [f'calib_acc_conf{i}' for i in range(1, 6)]
    missing = [c for c in conf_cols if c not in ps.columns]
    if missing:
        raise KeyError(f"per_subject.tsv missing calibration columns: {missing}")
    M = ps[conf_cols].apply(pd.to_numeric, errors='coerce')
    levels = [1, 2, 3, 4, 5]

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
        for _, row in M.iterrows():
            ax.plot(levels, row.to_numpy(dtype=float), color=C_FAINT,
                    lw=0.6, alpha=0.45, zorder=1)
        mean = M.mean(axis=0, skipna=True).to_numpy()
        q1 = M.quantile(0.25).to_numpy()
        q3 = M.quantile(0.75).to_numpy()
        ax.fill_between(levels, q1, q3, color=C_BLUE, alpha=0.18, zorder=2,
                        label='inter-quartile range')
        ax.plot(levels, mean, color=C_MEAN, lw=2.6, marker='o',
                markersize=6, zorder=4, label='cohort mean')
        ax.axhline(0.5, color=C_EXCLUDED, ls='--', lw=1.0, zorder=1,
                   label='chance')

        ax.set_xticks(levels)
        ax.set_xlabel('reported confidence')
        ax.set_ylabel('direction accuracy')
        ax.set_ylim(0.0, 1.03)
        ax.set_title(f'Confidence calibration  -  {len(M)} subjects')
        ax.legend(frameon=False, loc='upper left')
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - accuracy drift across main trials
# ---------------------------------------------------------------------------

def accuracy_drift_figure(
    responses_path: str | Path | None = None,
    per_subject_path: str | Path | None = None,
    save_path: str | Path | None = None,
    included_only: bool = True,
    figsize: tuple[float, float] = (7.8, 4.8),
):
    """Rolling-mean direction accuracy across the ordered real main
    trials. One faint line per subject; bold black = cohort mean;
    dashed verticals = main-block boundaries. Flat = no fatigue."""
    # Local import: only the drift figure needs the dashboard module.
    from .dashboard import (
        _per_trial_drift_curve, DRIFT_N_MAX, DRIFT_TRIALS_PER_BLOCK,
        DRIFT_N_BLOCKS, DRIFT_WINDOW,
    )
    if responses_path is None:
        responses_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'

    responses = pd.read_parquet(responses_path)
    curves = _per_trial_drift_curve(responses)

    ps = _prolific(pd.read_csv(per_subject_path, sep='\t'), included_only=included_only)
    keep = set(ps['pid_hash'].astype(str))
    series = {h: c for h, c in curves.items() if h in keep}

    xs = np.arange(1, DRIFT_N_MAX + 1)
    mat = np.full((len(series), DRIFT_N_MAX), np.nan)
    for r, c in enumerate(series.values()):
        arr = np.array([np.nan if v is None else v for v in c], dtype=float)
        mat[r, :len(arr)] = arr[:DRIFT_N_MAX]
    cohort_mean = np.nanmean(mat, axis=0) if len(mat) else np.full(DRIFT_N_MAX, np.nan)

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
        for row in mat:
            ax.plot(xs, row, color=C_FAINT, lw=0.5, alpha=0.4, zorder=1)
        ax.plot(xs, cohort_mean, color=C_MEAN, lw=2.6, zorder=3,
                label='cohort mean')

        for b in range(1, DRIFT_N_BLOCKS):
            xb = DRIFT_TRIALS_PER_BLOCK * b
            ax.axvline(xb, color='#999999', ls=(0, (4, 3)), lw=1.0, zorder=2)
            # Label just inside the top of the plot (clear of the title)
            # with a faint white background so it reads over the lines.
            ax.text(xb, 0.985, f'B{b} | B{b + 1}', fontsize=7, color='#555555',
                    ha='center', va='top', zorder=4,
                    bbox=dict(boxstyle='round,pad=0.15', fc='white',
                              ec='none', alpha=0.7))

        ax.set_xlabel('main trial index  (real trials only, ordered)')
        ax.set_ylabel(f'rolling direction accuracy  (window = {DRIFT_WINDOW})')
        ax.set_ylim(0.3, 1.0)
        ax.set_xlim(0, DRIFT_N_MAX)
        ax.set_title(f'Accuracy drift across main trials  -  {len(series)} subjects')
        ax.legend(frameon=False, loc='lower right')
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - per-video identifiability distribution
# ---------------------------------------------------------------------------

def identifiability_figure(
    per_video_path: str | Path | None = None,
    save_path: str | Path | None = None,
    figsize: tuple[float, float] = (7.0, 4.8),
):
    """Distribution of the per-(clip x direction) identifiability score,
    forward and backward overlaid as step histograms. A leftward shift
    of the backward distribution = reversed clips harder to identify
    (the Hanyu-et-al. signature at the per-clip level)."""
    if per_video_path is None:
        per_video_path = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    pv = pd.read_csv(per_video_path, sep='\t')

    bins = np.linspace(-1.0, 1.0, 33)
    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
        for direction, color in [('forward', C_BLUE), ('backward', C_ORANGE)]:
            v = pv.loc[pv['pm_direction'] == direction,
                       'identifiability_score'].dropna()
            ax.hist(v, bins=bins, histtype='stepfilled', lw=1.8,
                    edgecolor=color, facecolor=color + '22',
                    label=f'{direction}  (n = {len(v):,}, median {v.median():+.2f})')
        ax.axvline(0, color='#888888', lw=1.0, ls=':', zorder=1)

        ax.set_xlabel('identifiability score   '
                      '(-1 = systematically wrong  ...  +1 = confidently correct)')
        ax.set_ylabel('(stimulus x direction) cells')
        ax.set_xlim(-1.05, 1.05)
        ax.set_title('Per-video identifiability, by ground-truth direction')
        ax.legend(frameon=False, loc='upper left')
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - confidence vs accuracy ("fooler" view)
# ---------------------------------------------------------------------------

def confidence_accuracy_figure(
    per_video_path: str | Path | None = None,
    save_path: str | Path | None = None,
    min_views: int = 3,
    figsize: tuple[float, float] = (6.6, 6.0),
):
    """Mean confidence vs direction accuracy, one point per (clip x
    direction). Bottom-right = confident-but-wrong = the "fooler"
    clips that visually suggest the opposite temporal direction."""
    if per_video_path is None:
        per_video_path = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    pv = pd.read_csv(per_video_path, sep='\t')
    pv = pv[pv['n_views'] >= min_views].dropna(
        subset=['mean_confidence', 'direction_accuracy_raw'])

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

        # "Fooler" quadrant shading: high confidence, low accuracy.
        ax.axhspan(0.0, 0.5, xmin=0.5, xmax=1.0, color=C_EXCLUDED,
                   alpha=0.06, zorder=0)
        ax.axhline(0.5, color='#999999', lw=1.0, ls='--', zorder=1)
        ax.axvline(3.0, color='#dddddd', lw=0.8, zorder=1)

        for direction, color in [('forward', C_BLUE), ('backward', C_ORANGE)]:
            sub = pv[pv['pm_direction'] == direction]
            n = np.maximum(2, sub['n_views'].to_numpy())
            ax.scatter(sub['mean_confidence'], sub['direction_accuracy_raw'],
                       s=6 + 3 * np.log2(n), c=color, alpha=0.45,
                       edgecolors='none', zorder=3,
                       label=f'{direction}  (n = {len(sub):,})')

        ax.text(4.95, 0.03, 'confident, wrong\n("foolers")', fontsize=8,
                color=C_EXCLUDED, ha='right', va='bottom', style='italic')
        ax.set_xlabel('mean confidence on clip')
        ax.set_ylabel('direction accuracy')
        ax.set_xlim(1, 5)
        ax.set_ylim(0, 1.02)
        ax.set_title(f'Confidence vs accuracy per clip  (>= {min_views} views)')
        ax.legend(frameon=False, loc='lower left')
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - corpus coverage
# ---------------------------------------------------------------------------

def corpus_coverage_figure(
    per_video_path: str | Path | None = None,
    save_path: str | Path | None = None,
    corpus_target_cells: int = 4400,
    figsize: tuple[float, float] = (6.4, 4.4),
):
    """How many (stimulus x direction) cells have at least N viewings,
    against the n = 20 per-cell sampling target."""
    if per_video_path is None:
        per_video_path = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    pv = pd.read_csv(per_video_path, sep='\t')
    n_views = pv['n_views'].dropna().astype(int)

    thresholds = [1, 2, 3, 5, 10, 20]
    counts = [int((n_views >= n).sum()) for n in thresholds]

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
        bars = ax.bar([f'>= {n}' for n in thresholds], counts,
                      color=C_BLUE, alpha=0.85, edgecolor='white',
                      linewidth=0.6, width=0.7)
        ax.axhline(corpus_target_cells, color='#444444', lw=1.2, ls='--',
                   label=f'corpus target ({corpus_target_cells:,} cells)')
        for b, c in zip(bars, counts):
            ax.text(b.get_x() + b.get_width() / 2, c + corpus_target_cells * 0.012,
                    f'{c:,}', ha='center', va='bottom', fontsize=8)

        ax.set_xlabel('minimum viewings per (stimulus x direction) cell')
        ax.set_ylabel('cells')
        ax.set_ylim(0, corpus_target_cells * 1.12)
        ax.set_title('Corpus coverage')
        ax.legend(frameon=False, loc='upper right')
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Figure - inclusion / exclusion breakdown
# ---------------------------------------------------------------------------

def inclusion_figure(
    per_subject_path: str | Path | None = None,
    save_path: str | Path | None = None,
    figsize: tuple[float, float] = (7.2, 4.2),
):
    """Methods figure: how many Prolific subjects pass the inclusion
    gate, and which gate each excluded subject failed (a subject can
    fail more than one - bar counts are per-reason)."""
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    ps = _prolific(pd.read_csv(per_subject_path, sep='\t'))
    n = len(ps)
    n_inc = int(ps['included'].sum())
    exc = ps[ps['included'] != True]  # noqa: E712

    def reasons(r):
        out = []
        if r.get('n_blocks_completed', 4) < 4:
            out.append('partial session')
        if not (r.get('catch_direction_pass_rate', 0) >= 0.80):
            out.append('catch direction < .80')
        if not (r.get('qualification_direction_accuracy', 0) >= 0.75):
            out.append('qualification < .75')
        rt = r.get('median_direction_rt_main', np.nan)
        if not (250 <= rt <= 3000):
            out.append('RT out of range')
        if not (r.get('calib_gamma', -1) > 0):
            out.append('calibration gamma <= 0')
        if not (r.get('frac_play_completed', 0) >= 0.9):
            out.append('play_completed < .9')
        return out

    counts: dict[str, int] = {}
    for _, r in exc.iterrows():
        for reason in reasons(r):
            counts[reason] = counts.get(reason, 0) + 1
    items = sorted(counts.items(), key=lambda kv: kv[1])

    with _styled():
        fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)
        if items:
            labels, vals = zip(*items)
            bars = ax.barh(labels, vals, color=C_EXCLUDED, alpha=0.85,
                           edgecolor='white', linewidth=0.6, height=0.66)
            for b, v in zip(bars, vals):
                ax.text(v + max(vals) * 0.02, b.get_y() + b.get_height() / 2,
                        str(v), va='center', fontsize=8.5)
            ax.set_xlim(0, max(vals) * 1.15)
        ax.set_xlabel('excluded subjects failing this gate')
        ax.set_title(
            f'Inclusion gate  -  {n_inc} of {n} Prolific subjects included '
            f'({n_inc / max(1, n) * 100:.0f}%);  {n - n_inc} excluded',
        )
        _despine(ax)

    _save(fig, save_path)
    return fig


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

# Registry of (filename stem -> builder) for `python -m analysis.figures`.
_FIGURES = {
    'per_source_asymmetry': per_source_asymmetry_figure,
    'cohort_quality': cohort_quality_figure,
    'calibration': calibration_figure,
    'accuracy_drift': accuracy_drift_figure,
    'identifiability': identifiability_figure,
    'confidence_accuracy': confidence_accuracy_figure,
    'corpus_coverage': corpus_coverage_figure,
    'inclusion': inclusion_figure,
}


def build_all(out_dir: str | Path = PUB_DIR) -> None:
    """Regenerate every registered publication figure as a PDF."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stem, builder in _FIGURES.items():
        fig = builder(save_path=out_dir / f'{stem}.pdf')
        plt.close(fig)
    print(f"[figures] {len(_FIGURES)} figure(s) written to {out_dir}/")


if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')  # headless when run as a script
    build_all()

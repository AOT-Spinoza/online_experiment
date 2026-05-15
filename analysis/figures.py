"""
Publication-quality figures for the Arrow-of-Time experiment.

Where the interactive dashboard (analysis/dashboard.py) is Chart.js/HTML
for exploration, this module produces static **matplotlib** figures saved
as **vector PDFs** — for papers, posters, and slides. Each function reads
the derived TSVs the analysis pipeline already writes under
analysis/derived/, returns a matplotlib Figure, and (if `save_path` is
given) writes a PDF.

Driven by analysis/figures.ipynb. To regenerate every figure at once:

    python -m analysis.figures

Output PDFs land in analysis/derived/figures/pub/ (gitignored — they're
regeneratable artifacts; the code that makes them is what's version-
controlled).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize

from .load_all import DEFAULT_DERIVED_DIR

PUB_DIR = DEFAULT_DERIVED_DIR / 'figures' / 'pub'

# Diverging colormap matching the dashboard's asymmetry scatter:
# blue = reverse-render salient, grey = unremarkable, orange = forward
# render salient.
ASYMMETRY_CMAP = LinearSegmentedColormap.from_list(
    'aot_asymmetry', ['#2a6ea8', '#c9cdd1', '#c8702f'],
)

# Shared publication style — applied per-figure so importing the module
# doesn't mutate a notebook's global matplotlib state.
_PUB_RC = {
    'font.family': 'sans-serif',
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


# ---------------------------------------------------------------------------
# Figure 1 — per-source arrow-of-time asymmetry
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

    The static publication counterpart of the dashboard's asymmetry
    scatter. Axes are the two identifiability scores, plotted 1:1.

      - solid diagonal  y = x          raw symmetry
      - dashed diagonal y = x − offset bias-adjusted symmetry; the
        population forward-response bias shifts every clip off the
        solid line, so the dashed line is the real "no per-clip
        asymmetry" locus.
      - point colour    asymmetry_z_residual — arctanh-decompressed,
        forward-bias removed. Orange = the forward render carries the
        cleaner arrow-of-time cue; blue = the reversed render is the
        more conspicuous one (anti-gravity / anti-entropy events).
      - point size      min(n_views_fw, n_views_bw) — bigger = better
        sampled, more trustworthy.

    Parameters
    ----------
    min_views : drop sources with fewer than this many views in either
        direction (the asymmetry is too noisy below it).
    annotate_top : label this many most-extreme sources with their id.
    """
    if per_source_path is None:
        per_source_path = DEFAULT_DERIVED_DIR / 'per_source.tsv'
    df = pd.read_csv(per_source_path, sep='\t')

    needed = ['identifiability_score_fw', 'identifiability_score_bw',
              'asymmetry_z_residual']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise KeyError(
            f"per_source.tsv is missing {missing} — rerun "
            f"`python -m analysis.per_source` with the current code.",
        )
    df = df.dropna(subset=needed).copy()
    if min_views and {'n_views_fw', 'n_views_bw'}.issubset(df.columns):
        df = df[(df['n_views_fw'] >= min_views) & (df['n_views_bw'] >= min_views)]
    if not len(df):
        raise ValueError("no sources left to plot after the min_views filter")

    # Recover the raw forward-bias offset (constant: asymmetry − residual).
    offset_raw = 0.0
    if {'asymmetry', 'asymmetry_residual'}.issubset(df.columns):
        d = (df['asymmetry'] - df['asymmetry_residual']).dropna()
        if len(d):
            offset_raw = float(d.median())

    x = df['identifiability_score_fw'].to_numpy()
    y = df['identifiability_score_bw'].to_numpy()
    resid = df['asymmetry_z_residual'].to_numpy()

    # Symmetric colour limits at the 97th percentile of |residual| so a
    # few extreme clips don't wash the map out.
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
        # Zero axes (faint).
        ax.axhline(0, color='#e6e6e6', lw=0.7, zorder=0)
        ax.axvline(0, color='#e6e6e6', lw=0.7, zorder=0)
        # Reference diagonals.
        ax.plot(lim, lim, color='#b5b5b5', lw=1.0, zorder=1,
                label='raw symmetry  (y = x)')
        ax.plot(lim, [v - offset_raw for v in lim], color='#444444',
                lw=1.3, ls=(0, (5, 3)), zorder=1,
                label=f'bias-adjusted symmetry  (y = x − {offset_raw:.2f})')

        sc = ax.scatter(
            x, y, c=resid, cmap=ASYMMETRY_CMAP, norm=norm, s=sizes,
            edgecolors='#2c2c2c', linewidths=0.3, alpha=0.9, zorder=3,
        )

        # Annotate the most extreme sources. We walk points in
        # descending |residual| and greedily skip any whose marker sits
        # within `_collide` (data units) of an already-labelled point —
        # so genuinely-coincident extreme clips don't stack their labels.
        # Each kept label is offset *inward* (away from the nearest axis
        # edge) with a thin leader line.
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
            '← reversed render more conspicuous   ·   '
            'forward render carries cleaner cue →',
            fontsize=8.5,
        )

        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_aspect('equal', adjustable='box')
        ax.set_xlabel('forward-render identifiability')
        ax.set_ylabel('backward-render identifiability')
        ax.set_title('Per-source arrow-of-time asymmetry', pad=10)
        ax.legend(loc='upper left', frameon=False, handlelength=2.4)

        # n + forward-bias offset, placed inside the (sparse) lower-left
        # region so it's always visible regardless of layout engine.
        ax.text(
            -1.0, -0.60,
            f'n = {len(df):,} sources  (≥ {min_views} views / direction)\n'
            f'forward-bias offset = +{offset_raw:.2f}  (gap between diagonals)',
            fontsize=7.5, color='#555555', ha='left', va='top',
            bbox=dict(boxstyle='round,pad=0.35', fc='white',
                      ec='#dddddd', lw=0.6),
        )

    if save_path is not None:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, format='pdf')
        print(f"[figures] wrote {save_path}")
    return fig


# ---------------------------------------------------------------------------
# Batch entry point
# ---------------------------------------------------------------------------

# Registry of (filename stem, builder) for `python -m analysis.figures`.
# Add new publication figures here as they're written.
_FIGURES = {
    'per_source_asymmetry': per_source_asymmetry_figure,
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

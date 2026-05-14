"""
Generate analysis/derived/dashboard.html — a self-contained interactive
dashboard for monitoring across-subject data quality as Prolific
participants accumulate.

Reads:
  - analysis/derived/per_subject.tsv   (from analysis.per_subject)
  - analysis/derived/per_video.tsv     (from analysis.per_video)

Writes:
  - analysis/derived/dashboard.html    (open in any browser; no server)

The HTML is a single file with everything embedded — JSON data, Chart.js
via CDN, CSS, and JS. Filter toggles (All / Prolific-only / Included-only)
re-render KPIs and charts in the browser. The subject table is sortable
and click-to-highlight: clicking a row foregrounds that subject's
calibration curve in the calibration chart.

Usage:
    python -m analysis.dashboard

    # or from a notebook
    from analysis.dashboard import build_dashboard
    build_dashboard()
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .load_all import DEFAULT_DERIVED_DIR

# Drift chart parameters. The cohort target is 4 × 95 = 380 real main
# trials; subjects who didn't reach the full count are NaN-padded so all
# curves share the same x-axis in the dashboard.
DRIFT_TRIALS_PER_BLOCK = 95   # real (non-catch) trials per main block
DRIFT_N_BLOCKS = 4
DRIFT_N_MAX = DRIFT_TRIALS_PER_BLOCK * DRIFT_N_BLOCKS  # 380
DRIFT_WINDOW = 25             # rolling mean width, centred


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def _clean_subjects(ps: pd.DataFrame) -> list[dict]:
    """Convert per_subject.tsv rows into JSON-safe dicts for the browser.

    Adds a `calib_curve` list (accuracy at conf 1..5) and `calib_n` list so
    the calibration chart can plot per-subject curves directly without
    re-running the calibration logic in JS.
    """
    out = []
    for _, r in ps.iterrows():
        rec = {}
        for col, val in r.items():
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, (np.bool_, bool)):
                rec[col] = bool(val)
            elif isinstance(val, (np.integer,)):
                rec[col] = int(val)
            elif isinstance(val, (np.floating, float)):
                f = float(val)
                rec[col] = None if (np.isnan(f) or np.isinf(f)) else f
            else:
                rec[col] = str(val)
        rec['is_prolific'] = not str(r['pid']).startswith('LOCAL_')
        rec['calib_curve'] = [rec.get(f'calib_acc_conf{i}') for i in range(1, 6)]
        rec['calib_n'] = [rec.get(f'calib_n_conf{i}', 0) for i in range(1, 6)]
        # Friendly session duration in minutes for the table.
        sess_ms = rec.get('total_session_ms')
        rec['total_session_min'] = (sess_ms / 60000.0) if sess_ms else None
        out.append(rec)
    return out


def _per_trial_drift_curve(
    responses: pd.DataFrame,
    window: int = DRIFT_WINDOW,
    n_max: int = DRIFT_N_MAX,
) -> dict[str, list]:
    """Per-subject rolling-mean direction accuracy across real main trials.

    Trials are ordered (block_index, trial_index_in_block), the centered
    rolling window is applied across that sequence (skipping the catch
    rows because pm_type='main' excludes them upstream), and the result
    is padded with None to a common length of `n_max` so all subject
    curves can share a single x-axis in the dashboard.

    Used by the drift chart to spot fatigue (down-slope), warm-up
    (up-slope), or a sudden anomaly inside one block.
    """
    if 'pid_hash' not in responses.columns:
        return {}
    m = responses[
        (responses['trial_type_tag'] == 'stimulus')
        & (responses['phase'] == 'main')
        & (responses['pm_type'] == 'main')
    ].copy()
    if not len(m) or 'main_correct' not in m.columns:
        return {}
    m['main_correct'] = m['main_correct'].astype('boolean')
    out: dict[str, list] = {}
    for pid_hash, sub in m.groupby('pid_hash'):
        ordered = sub.sort_values(['block_index', 'trial_index_in_block']).reset_index(drop=True)
        acc = ordered['main_correct'].astype('Int64').astype('float64')
        rolling = acc.rolling(
            window=window, min_periods=max(5, window // 2), center=True,
        ).mean().tolist()
        padded = rolling[:n_max] + [None] * max(0, n_max - len(rolling))
        out[str(pid_hash)] = [
            None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
            for v in padded
        ]
    return out


def _source_asymmetry(per_source: pd.DataFrame) -> list[dict]:
    """Convert per_source.tsv into JSON records for the asymmetry scatter.
    Only sources with both forward AND backward viewings are included
    (else the asymmetry isn't defined)."""
    out = []
    for _, r in per_source.iterrows():
        fw = r.get('identifiability_score_fw')
        bw = r.get('identifiability_score_bw')
        if pd.isna(fw) or pd.isna(bw):
            continue
        out.append({
            'source_id': str(r['source_id']),
            'id_fw': float(fw),
            'id_bw': float(bw),
            'asymmetry': float(r.get('asymmetry') or 0.0),
            'n_views_fw': int(r.get('n_views_fw') or 0),
            'n_views_bw': int(r.get('n_views_bw') or 0),
            'preferred_direction': str(r.get('preferred_direction') or 'tied'),
        })
    return out


def _video_aggregates(pv: pd.DataFrame, corpus_target_cells: int = 4400) -> dict:
    """Pre-compute corpus-level stats so we don't ship 4k rows of per-video
    data to the browser when only a handful of histograms are needed."""

    n_views = pv['n_views'].dropna().astype(int)
    n_views_hist = {int(k): int(v) for k, v in n_views.value_counts().sort_index().items()}

    coverage_at_n = {}
    for n in [1, 2, 3, 5, 10, 20]:
        coverage_at_n[str(n)] = int((n_views >= n).sum())

    # Identifiability score histogram (only on cells with a valid score).
    id_scores = pv['identifiability_score'].dropna()
    bin_edges = np.linspace(-1.0, 1.0, 21)  # 20 bins
    counts, _ = np.histogram(id_scores, bins=bin_edges)
    id_hist = [
        {'bin_lo': float(bin_edges[i]),
         'bin_hi': float(bin_edges[i + 1]),
         'bin_center': float((bin_edges[i] + bin_edges[i + 1]) / 2),
         'count': int(counts[i])}
        for i in range(len(counts))
    ]

    # Raw accuracy histogram — over cells with ≥ 2 views (single-view cells
    # are trivially 0/1 and dominate the histogram).
    raw = pv.loc[pv['n_views'] >= 2, 'direction_accuracy_raw'].dropna()
    raw_edges = np.linspace(0.0, 1.0, 11)
    raw_counts, _ = np.histogram(raw, bins=raw_edges)
    raw_hist = [
        {'bin_lo': float(raw_edges[i]),
         'bin_hi': float(raw_edges[i + 1]),
         'count': int(raw_counts[i])}
        for i in range(len(raw_counts))
    ]

    return {
        'n_views_hist': n_views_hist,
        'coverage_at_n': coverage_at_n,
        'id_score_hist': id_hist,
        'raw_acc_hist': raw_hist,
        'total_cells_observed': int(len(pv)),
        'total_cells_corpus': corpus_target_cells,
        'mean_n_views': float(n_views.mean()) if len(n_views) else 0.0,
        'median_id_score': float(id_scores.median()) if len(id_scores) else 0.0,
    }


def build_dashboard(
    per_subject_path: str | Path | None = None,
    per_video_path: str | Path | None = None,
    per_source_path: str | Path | None = None,
    responses_path: str | Path | None = None,
    out_path: str | Path | None = None,
) -> Path:
    if per_subject_path is None:
        per_subject_path = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    if per_video_path is None:
        per_video_path = DEFAULT_DERIVED_DIR / 'per_video.tsv'
    if per_source_path is None:
        per_source_path = DEFAULT_DERIVED_DIR / 'per_source.tsv'
    if responses_path is None:
        responses_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    if out_path is None:
        out_path = DEFAULT_DERIVED_DIR / 'dashboard.html'
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ps = pd.read_csv(per_subject_path, sep='\t')
    pv = pd.read_csv(per_video_path, sep='\t')

    subjects = _clean_subjects(ps)
    # Compute per-trial rolling drift from the parquet and attach a
    # curve (one value per ordered real main trial) to each subject so
    # the drift chart can plot lines directly without ever touching the
    # raw 60k-row store in JS.
    trial_drift: dict[str, list] = {}
    if Path(responses_path).exists():
        responses = pd.read_parquet(responses_path)
        trial_drift = _per_trial_drift_curve(responses)
    for s in subjects:
        s['trial_drift'] = trial_drift.get(
            s['pid_hash'], [None] * DRIFT_N_MAX,
        )

    per_source = pd.read_csv(per_source_path, sep='\t') if Path(per_source_path).exists() else pd.DataFrame()
    per_source_records = _source_asymmetry(per_source) if len(per_source) else []

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'subjects': subjects,
        'per_video': _video_aggregates(pv),
        'per_source': per_source_records,
        'drift': {
            'n_max': DRIFT_N_MAX,
            'window': DRIFT_WINDOW,
            'block_boundaries': [DRIFT_TRIALS_PER_BLOCK * i for i in range(1, DRIFT_N_BLOCKS)],
        },
    }

    html = _HTML_TEMPLATE.replace(
        '__PAYLOAD_JSON__',
        json.dumps(payload, allow_nan=False),
    )
    out_path.write_text(html)
    print(f"[dashboard] wrote {out_path}  ({len(payload['subjects'])} subjects, "
          f"{payload['per_video']['total_cells_observed']:,} per-video cells)")
    return out_path


# ---------------------------------------------------------------------------
# HTML template (single self-contained file)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AoT cohort dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1"
        integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ"
        crossorigin="anonymous"></script>
<style>
:root {
    --bg: #f6f7f9;
    --card: #ffffff;
    --header: #1d2733;
    --text: #1a1a1a;
    --muted: #6b7280;
    --grid: #e5e7eb;
    --included: #2a8c2a;
    --excluded: #c43b3b;
    --included-bg: #e7f5e7;
    --excluded-bg: #fce8e8;
    --accent: #2a6e8c;
    --accent-2: #c08040;
    --gap: 16px;
    --radius: 8px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.45;
    padding: 16px;
}
.container { max-width: 1500px; margin: 0 auto; }

header.bar {
    background: var(--header);
    color: white;
    padding: 18px 22px;
    border-radius: var(--radius);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: var(--gap);
}
header.bar h1 { font-size: 19px; font-weight: 600; }
header.bar .sub { font-size: 12px; color: rgba(255,255,255,.65); margin-top: 2px; }
header.bar .filters { display: flex; gap: 8px; align-items: center; }
header.bar .filters label { font-size: 12px; color: rgba(255,255,255,.7); }
header.bar select {
    padding: 6px 10px;
    border: 1px solid rgba(255,255,255,.25);
    border-radius: 4px;
    background: rgba(255,255,255,.08);
    color: white;
    font-size: 13px;
}
header.bar select option { background: var(--header); color: white; }

.kpi-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: var(--gap);
    margin-bottom: var(--gap);
}
.kpi-card {
    background: var(--card);
    border-radius: var(--radius);
    padding: 16px 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,.06);
}
.kpi-label {
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.4px;
    margin-bottom: 4px;
}
.kpi-value { font-size: 26px; font-weight: 700; }
.kpi-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }

.chart-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
    gap: var(--gap);
    margin-bottom: var(--gap);
}
.chart-card {
    background: var(--card);
    border-radius: var(--radius);
    padding: 16px 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,.06);
}
.chart-card h3 {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
}
.chart-card .desc {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 14px;
}
.chart-card .canvas-wrap {
    position: relative;
    height: 260px;
}
.chart-card canvas { max-height: 260px; }

.table-card {
    background: var(--card);
    border-radius: var(--radius);
    padding: 16px 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,.06);
    overflow-x: auto;
    margin-bottom: var(--gap);
}
.table-card h3 {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 12px;
}
.table-card .hint {
    font-size: 11px;
    color: var(--muted);
    margin-bottom: 12px;
}
table { width: 100%; border-collapse: collapse; font-size: 12px; }
thead th {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 2px solid var(--grid);
    color: var(--muted);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
}
thead th:hover { color: var(--text); background: #f3f4f6; }
tbody td { padding: 7px 10px; border-bottom: 1px solid #f0f0f0; }
tbody tr.included { background: #f6fcf6; }
tbody tr.excluded { background: #fdf2f2; }
tbody tr:hover { background: #eef3fa; cursor: pointer; }
tbody tr.highlight { background: #fff3c4 !important; }

.pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
}
.pill.included { background: var(--included-bg); color: var(--included); }
.pill.excluded { background: var(--excluded-bg); color: var(--excluded); }
.pill.partial  { background: #fff4cf; color: #8a6a00; }

.right { text-align: right; }
.center { text-align: center; }
.mono { font-family: 'SF Mono', Menlo, Consolas, monospace; }
.muted-text { color: var(--muted); }
</style>
</head>
<body>
<div class="container">

<header class="bar">
    <div>
        <h1>Arrow-of-Time online experiment — cohort dashboard</h1>
        <div class="sub">Across-subject data-quality monitor. Updated <span id="updated"></span>.</div>
    </div>
    <div class="filters">
        <label for="filter-scope">Show:</label>
        <select id="filter-scope">
            <option value="prolific">Prolific only</option>
            <option value="all">All sessions (incl. dev)</option>
            <option value="included">Included only</option>
        </select>
    </div>
</header>

<section class="kpi-row" id="kpi-row"></section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Inclusion gate outcome</h3>
        <div class="desc">Hard §3.9 inclusion gate: catch ≥ .80, qualification ≥ .75, RT 250–3000 ms, γ &gt; 0, play_completed ≥ .90.</div>
        <div class="canvas-wrap"><canvas id="ch-inclusion"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Why subjects were excluded</h3>
        <div class="desc">Reasons fail counts (each subject can fail multiple gates).</div>
        <div class="canvas-wrap"><canvas id="ch-exclusion"></canvas></div>
    </div>
</section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Type-1 sensitivity (d′)</h3>
        <div class="desc">Can they discriminate forward from backward? d′ &gt; 1 = strong.</div>
        <div class="canvas-wrap"><canvas id="ch-dprime"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Metacognitive efficiency (M-ratio)</h3>
        <div class="desc">meta-d′ / d′. 1.0 = ideal; &lt; 0 = anti-metacognitive.</div>
        <div class="canvas-wrap"><canvas id="ch-mratio"></canvas></div>
    </div>
</section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Catch-trial pass rate</h3>
        <div class="desc">Direction AND confidence correct. Inclusion threshold = 0.80.</div>
        <div class="canvas-wrap"><canvas id="ch-catch"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Forward bias</h3>
        <div class="desc">P(say forward) − 0.5, on real main trials. Hanyu et al. reported large + bias.</div>
        <div class="canvas-wrap"><canvas id="ch-fwd"></canvas></div>
    </div>
</section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Calibration: accuracy by reported confidence</h3>
        <div class="desc">Each faint line = one subject. Bold = cohort mean. Click a row in the table below to highlight that subject.</div>
        <div class="canvas-wrap"><canvas id="ch-calib"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Accuracy drift across main trials (rolling)</h3>
        <div class="desc">25-trial centred rolling-mean direction accuracy across real main trials, ordered by (block, trial). Dashed verticals = block boundaries. Flat = no drift; rising = warm-up; falling = fatigue.</div>
        <div class="canvas-wrap"><canvas id="ch-drift"></canvas></div>
    </div>
</section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Per-video identifiability score</h3>
        <div class="desc">Confidence-weighted bettor score per (stimulus × direction). Bins of width 0.1; green = correctly identified, red = systematically misidentified.</div>
        <div class="canvas-wrap"><canvas id="ch-id"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Per-source asymmetry: forward vs backward identifiability</h3>
        <div class="desc">One point per source video. On-diagonal = same difficulty in both directions; off-diagonal = asymmetric (one direction much easier). Hover for source_id + scores.</div>
        <div class="canvas-wrap"><canvas id="ch-asymmetry"></canvas></div>
    </div>
</section>

<section class="chart-row">
    <div class="chart-card">
        <h3>Corpus coverage</h3>
        <div class="desc">Cumulative count of (stim × direction) cells with at least N viewings. Target = 20 per cell.</div>
        <div class="canvas-wrap"><canvas id="ch-coverage"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Session duration distribution</h3>
        <div class="desc">Wall-clock minutes from welcome to final save. 90-min soft cap.</div>
        <div class="canvas-wrap"><canvas id="ch-duration"></canvas></div>
    </div>
</section>

<section class="table-card">
    <h3>Per-subject table</h3>
    <div class="hint">Click any column header to sort. Click a row to highlight that subject's calibration curve above.</div>
    <table id="subject-table">
        <thead><tr id="thead-row"></tr></thead>
        <tbody id="tbody"></tbody>
    </table>
</section>

<footer style="font-size:11px; color:var(--muted); padding:8px 0 16px;">
    Data regenerated from <code>analysis/derived/per_subject.tsv</code> +
    <code>analysis/derived/per_video.tsv</code>. Open with
    <code>python -m analysis.dashboard</code>.
</footer>

</div>

<script>
const PAYLOAD = __PAYLOAD_JSON__;

const COLORS = {
    included: '#2a8c2a',
    excluded: '#c43b3b',
    accent:   '#2a6e8c',
    accent2:  '#c08040',
    grey:     '#9aa1a8',
    highlight:'#d99a14',
};

document.getElementById('updated').textContent =
    new Date(PAYLOAD.generated_at).toLocaleString();

let scope = 'prolific';
let highlightPid = null;
const charts = {};
const TABLE_COLS = [
    { key: 'pid_hash',                    label: 'subject',  fmt: 'str' },
    { key: 'is_prolific',                 label: 'prolific', fmt: 'bool' },
    { key: 'included',                    label: 'inc',      fmt: 'pill' },
    { key: 'n_blocks_completed',          label: 'blocks',   fmt: 'int' },
    { key: 'subject_quality_weight',      label: 'qual w',   fmt: 'f3' },
    { key: 'catch_full_pass_rate',        label: 'catch',    fmt: 'f2' },
    { key: 'd_prime',                     label: "d'",       fmt: 'f2' },
    { key: 'meta_d_prime',                label: "meta-d'",  fmt: 'f2' },
    { key: 'm_ratio',                     label: 'M-ratio',  fmt: 'f2' },
    { key: 'calib_gamma',                 label: 'γ',        fmt: 'f2' },
    { key: 'main_direction_accuracy',     label: 'main acc', fmt: 'f2' },
    { key: 'forward_bias',                label: 'fwd bias', fmt: 'fbias' },
    { key: 'frac_missed_main',            label: 'missed',   fmt: 'f3' },
    { key: 'frac_fast_lapses',            label: 'fast laps',fmt: 'f3' },
    { key: 'median_direction_rt_main',    label: 'RT ms',    fmt: 'int' },
    { key: 'total_session_min',           label: 'mins',     fmt: 'f0' },
];
let sortCol = 'subject_quality_weight';
let sortDir = 'desc';

// ---------- helpers ----------
function activeSubjects() {
    return PAYLOAD.subjects.filter(s => {
        if (scope === 'all')      return true;
        if (scope === 'prolific') return s.is_prolific;
        if (scope === 'included') return s.is_prolific && s.included;
    });
}
function fmt(v, kind) {
    if (v === null || v === undefined) return '—';
    if (kind === 'str')  return v;
    if (kind === 'bool') return v ? 'yes' : 'no';
    if (kind === 'int')  return Number(v).toFixed(0);
    if (kind === 'f0')   return Number(v).toFixed(0);
    if (kind === 'f2')   return Number(v).toFixed(2);
    if (kind === 'f3')   return Number(v).toFixed(3);
    if (kind === 'fbias') {
        const n = Number(v);
        return (n >= 0 ? '+' : '') + n.toFixed(2);
    }
    if (kind === 'pill') {
        const cls = v ? 'included' : 'excluded';
        const label = v ? 'INC' : 'EXC';
        return `<span class="pill ${cls}">${label}</span>`;
    }
    return v.toString();
}
function median(xs) {
    const a = xs.filter(v => v !== null && !isNaN(v)).sort((p,q) => p - q);
    if (a.length === 0) return null;
    const mid = Math.floor(a.length / 2);
    return a.length % 2 ? a[mid] : (a[mid-1] + a[mid]) / 2;
}
function mean(xs) {
    const a = xs.filter(v => v !== null && !isNaN(v));
    return a.length ? a.reduce((s,v) => s+v, 0) / a.length : null;
}
function histogram(values, edges) {
    const counts = new Array(edges.length - 1).fill(0);
    for (const v of values) {
        if (v === null || isNaN(v)) continue;
        for (let i = 0; i < edges.length - 1; i++) {
            const inLast = (i === edges.length - 2) && v === edges[i+1];
            if ((v >= edges[i] && v < edges[i+1]) || inLast) {
                counts[i] += 1;
                break;
            }
        }
    }
    return counts;
}

// Build the list of failure reasons for a subject (mirrors per_subject._session_metrics).
function exclusionReasons(s) {
    const r = [];
    if (s.n_blocks_completed != null && s.n_blocks_completed < 4) r.push(`only ${s.n_blocks_completed}/4 blocks`);
    if (s.catch_full_pass_rate == null || s.catch_full_pass_rate < 0.80) r.push('catch < .80');
    if (s.qualification_direction_accuracy == null || s.qualification_direction_accuracy < 0.75) r.push('qualification < .75');
    if (s.median_direction_rt_main == null || s.median_direction_rt_main < 250 || s.median_direction_rt_main > 3000) r.push('RT outside [250, 3000] ms');
    if (s.calib_gamma == null || s.calib_gamma <= 0) r.push('γ ≤ 0');
    if (s.frac_play_completed == null || s.frac_play_completed < 0.9) r.push('play_completed < .9');
    return r;
}

// ---------- KPIs ----------
function renderKpis() {
    const subs = activeSubjects();
    const prolific = subs.filter(s => s.is_prolific);
    const included = subs.filter(s => s.included);
    const totalMain = subs.reduce((s,r) => s + (r.n_main_real || 0), 0);
    const cov3 = PAYLOAD.per_video.coverage_at_n['3'] || 0;
    const corpus = PAYLOAD.per_video.total_cells_corpus;

    const kpis = [
        { label: 'subjects (scope)',     value: subs.length,
          sub: `${prolific.length} prolific · ${subs.length - prolific.length} dev` },
        { label: 'included',             value: included.length,
          sub: `${(included.length / Math.max(1, subs.length) * 100).toFixed(0)}% pass rate` },
        { label: 'main trials collected',value: totalMain.toLocaleString() },
        { label: "median d'",            value: (median(subs.map(s => s.d_prime)) ?? 0).toFixed(2) },
        { label: 'median M-ratio',       value: (median(subs.map(s => s.m_ratio)) ?? 0).toFixed(2) },
        { label: 'median fwd bias',      value: (() => {
              const m = median(subs.map(s => s.forward_bias));
              if (m === null) return '—';
              return (m >= 0 ? '+' : '') + m.toFixed(2);
          })() },
        { label: 'corpus cells ≥3 views',value: cov3.toLocaleString(),
          sub: `${(cov3 / corpus * 100).toFixed(0)}% of ${corpus.toLocaleString()} cells` },
    ];
    document.getElementById('kpi-row').innerHTML = kpis.map(k => `
        <div class="kpi-card">
            <div class="kpi-label">${k.label}</div>
            <div class="kpi-value">${k.value}</div>
            ${k.sub ? `<div class="kpi-sub">${k.sub}</div>` : ''}
        </div>
    `).join('');
}

// ---------- charts ----------
function destroy(name) { if (charts[name]) { charts[name].destroy(); delete charts[name]; } }

function chartInclusion() {
    destroy('inclusion');
    const subs = activeSubjects();
    const inc = subs.filter(s => s.included).length;
    const exc = subs.length - inc;
    charts.inclusion = new Chart(document.getElementById('ch-inclusion'), {
        type: 'doughnut',
        data: {
            labels: ['Included', 'Excluded'],
            datasets: [{
                data: [inc, exc],
                backgroundColor: [COLORS.included, COLORS.excluded],
                borderColor: '#fff',
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '60%',
            plugins: {
                legend: { position: 'right', labels: { padding: 14, usePointStyle: true } },
                tooltip: {
                    callbacks: {
                        label: c => {
                            const tot = c.dataset.data.reduce((a,b)=>a+b,0);
                            const pct = tot ? (c.parsed / tot * 100).toFixed(0) : 0;
                            return `${c.label}: ${c.parsed} (${pct}%)`;
                        },
                    },
                },
            },
        },
    });
}

function chartExclusion() {
    destroy('exclusion');
    const subs = activeSubjects();
    const counts = {};
    for (const s of subs) {
        if (s.included) continue;
        for (const r of exclusionReasons(s)) counts[r] = (counts[r] || 0) + 1;
    }
    const labels = Object.keys(counts);
    const vals = labels.map(l => counts[l]);
    charts.exclusion = new Chart(document.getElementById('ch-exclusion'), {
        type: 'bar',
        data: { labels, datasets: [{
            data: vals,
            backgroundColor: COLORS.excluded + 'CC',
            borderColor: COLORS.excluded,
            borderWidth: 1,
            borderRadius: 4,
        }]},
        options: {
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, ticks: { precision: 0 } },
                y: { ticks: { font: { size: 11 } } },
            },
        },
    });
}

function chartHistogram(name, canvasId, values, edges, opts = {}) {
    destroy(name);
    const counts = histogram(values, edges);
    const labels = edges.slice(0, -1).map((e, i) => {
        const lo = e.toFixed(opts.label_decimals ?? 2);
        const hi = edges[i+1].toFixed(opts.label_decimals ?? 2);
        return `${lo}`;
    });
    const ann = opts.threshold;
    const datasets = [{
        data: counts,
        backgroundColor: (opts.barColor || COLORS.accent) + 'CC',
        borderColor: opts.barColor || COLORS.accent,
        borderWidth: 1,
        borderRadius: 3,
    }];
    charts[name] = new Chart(document.getElementById(canvasId), {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => {
                            const i = items[0].dataIndex;
                            const lo = edges[i].toFixed(opts.label_decimals ?? 2);
                            const hi = edges[i+1].toFixed(opts.label_decimals ?? 2);
                            return `${lo} – ${hi}`;
                        },
                        label: c => `n = ${c.parsed.y}`,
                    },
                },
                annotation: {},  // placeholder
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
                    title: opts.x_title ? { display: true, text: opts.x_title, font: { size: 11 } } : undefined,
                },
                y: { beginAtZero: true, ticks: { precision: 0 } },
            },
        },
    });
    // Manual threshold/median line — simplest path is a second bar dataset
    // pointing at the bin that contains the line, but a proper vertical line
    // requires the annotation plugin. Skip for now; the median and threshold
    // are visible in the KPI row / inclusion bar.
}

function chartDPrime() {
    const subs = activeSubjects();
    const edges = [-1, -0.5, 0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
    chartHistogram('dprime', 'ch-dprime', subs.map(s => s.d_prime), edges,
        { barColor: COLORS.accent, x_title: "d' bin lower edge", label_decimals: 2 });
}
function chartMRatio() {
    const subs = activeSubjects();
    const edges = [-1, -0.5, 0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5];
    chartHistogram('mratio', 'ch-mratio', subs.map(s => s.m_ratio), edges,
        { barColor: COLORS.accent2, x_title: 'M-ratio bin lower edge', label_decimals: 2 });
}
function chartCatch() {
    const subs = activeSubjects();
    const edges = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.001];
    chartHistogram('catch', 'ch-catch', subs.map(s => s.catch_full_pass_rate), edges,
        { barColor: COLORS.accent, x_title: 'catch pass rate (0.80 threshold)', label_decimals: 2 });
}
function chartForward() {
    const subs = activeSubjects();
    const edges = [-0.5, -0.3, -0.2, -0.1, -0.05, 0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5];
    chartHistogram('fwd', 'ch-fwd', subs.map(s => s.forward_bias), edges,
        { barColor: COLORS.accent2, x_title: 'forward bias', label_decimals: 2 });
}

function chartCalibration() {
    destroy('calib');
    const subs = activeSubjects();
    const labels = ['1','2','3','4','5'];

    // Cohort mean line at each confidence.
    const means = [0,1,2,3,4].map(i =>
        mean(subs.map(s => s.calib_curve ? s.calib_curve[i] : null))
    );

    const datasets = subs.map(s => ({
        label: s.pid_hash,
        data: s.calib_curve || [null,null,null,null,null],
        borderColor: s.pid_hash === highlightPid ? COLORS.highlight :
                     (s.included ? COLORS.included + '66' : COLORS.excluded + '66'),
        backgroundColor: 'transparent',
        borderWidth: s.pid_hash === highlightPid ? 3 : 1,
        tension: 0.2,
        pointRadius: s.pid_hash === highlightPid ? 4 : 1.5,
        spanGaps: true,
    }));
    datasets.push({
        label: 'cohort mean',
        data: means,
        borderColor: '#111',
        backgroundColor: 'transparent',
        borderWidth: 3,
        tension: 0.2,
        pointRadius: 5,
        pointBackgroundColor: '#111',
    });

    charts.calib = new Chart(document.getElementById('ch-calib'), {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => `confidence = ${items[0].label}`,
                        label: c => {
                            const v = c.parsed.y;
                            return c.dataset.label === 'cohort mean'
                                ? `cohort mean: ${v?.toFixed?.(2) ?? '—'}`
                                : `${c.dataset.label}: ${v?.toFixed?.(2) ?? '—'}`;
                        },
                    },
                },
            },
            scales: {
                y: { min: 0, max: 1, title: { display: true, text: 'direction accuracy' }, ticks: { stepSize: 0.2 } },
                x: { title: { display: true, text: 'reported confidence' } },
            },
        },
    });
}

// Per-chart plugin that paints dashed verticals at block boundaries on
// the drift chart. Cleaner than the chartjs-plugin-annotation dependency.
const blockBoundaryPlugin = {
    id: 'blockBoundary',
    afterDraw: (chart, args, opts) => {
        const xs = opts?.boundaries;
        if (!Array.isArray(xs) || xs.length === 0) return;
        const ctx = chart.ctx;
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        ctx.save();
        ctx.strokeStyle = '#888';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.font = '10px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillStyle = '#666';
        ctx.textAlign = 'center';
        xs.forEach((b, i) => {
            const px = xScale.getPixelForValue(b - 1);  // labels are 1-indexed
            ctx.beginPath();
            ctx.moveTo(px, yScale.top);
            ctx.lineTo(px, yScale.bottom);
            ctx.stroke();
            ctx.fillText(`B${i + 1}|B${i + 2}`, px, yScale.top - 2);
        });
        ctx.restore();
    },
};

function chartDrift() {
    destroy('drift');
    const subs = activeSubjects();
    const N_MAX = (PAYLOAD.drift && PAYLOAD.drift.n_max) || 380;
    const boundaries = (PAYLOAD.drift && PAYLOAD.drift.block_boundaries) || [95, 190, 285];
    const labels = Array.from({ length: N_MAX }, (_, i) => i + 1);

    // Cohort mean at each trial position, averaged over subjects with
    // a non-null rolling value at that index.
    const means = [];
    for (let i = 0; i < N_MAX; i++) {
        const vals = subs
            .map(s => (s.trial_drift && s.trial_drift[i] != null) ? s.trial_drift[i] : null)
            .filter(v => v != null && !isNaN(v));
        means.push(vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null);
    }

    const datasets = subs.map(s => ({
        label: s.pid_hash,
        data: s.trial_drift || new Array(N_MAX).fill(null),
        borderColor: s.pid_hash === highlightPid ? COLORS.highlight :
                     (s.included ? COLORS.included + '44' : COLORS.excluded + '44'),
        backgroundColor: 'transparent',
        borderWidth: s.pid_hash === highlightPid ? 2.5 : 0.8,
        tension: 0.15,
        pointRadius: 0,
        pointHoverRadius: 0,
        spanGaps: true,
        order: s.pid_hash === highlightPid ? 0 : 2,
    }));
    datasets.push({
        label: 'cohort mean',
        data: means,
        borderColor: '#111',
        backgroundColor: 'transparent',
        borderWidth: 2.5,
        tension: 0.15,
        pointRadius: 0,
        spanGaps: true,
        order: 1,
    });

    charts.drift = new Chart(document.getElementById('ch-drift'), {
        type: 'line',
        data: { labels, datasets },
        plugins: [blockBoundaryPlugin],
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: false,            // 30+ lines × 380 points → skip animation
            interaction: { mode: 'nearest', axis: 'x', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => `trial ${items[0].label}`,
                        label: c => {
                            const v = c.parsed.y;
                            if (v == null || isNaN(v)) return null;
                            return c.dataset.label === 'cohort mean'
                                ? `cohort mean: ${v.toFixed(2)}`
                                : `${c.dataset.label}: ${v.toFixed(2)}`;
                        },
                    },
                },
                blockBoundary: { boundaries: boundaries },
            },
            scales: {
                y: {
                    min: 0.3, max: 1.0,
                    title: { display: true, text: `rolling accuracy (window=${(PAYLOAD.drift && PAYLOAD.drift.window) || 25})` },
                    ticks: { stepSize: 0.1 },
                },
                x: {
                    title: { display: true, text: 'main trial index (real only, ordered)' },
                    ticks: { maxTicksLimit: 10, autoSkip: true, font: { size: 10 } },
                    grid: { display: false },
                },
            },
        },
    });
}

function chartAsymmetry() {
    destroy('asymmetry');
    const rows = PAYLOAD.per_source || [];
    // Color by absolute asymmetry: large = orange, small = blue. Size by total views.
    const points = rows.map(r => ({
        x: r.id_fw,
        y: r.id_bw,
        _row: r,
    }));
    const colors = rows.map(r => {
        const a = Math.abs(r.asymmetry);
        if (a >= 1.0) return COLORS.accent2 + 'CC';   // very asymmetric
        if (a >= 0.5) return '#e6a542CC';            // moderately
        return COLORS.accent + '55';                  // near-diagonal
    });
    const sizes = rows.map(r => {
        const n = (r.n_views_fw || 0) + (r.n_views_bw || 0);
        return Math.min(8, 2 + n);
    });

    charts.asymmetry = new Chart(document.getElementById('ch-asymmetry'), {
        type: 'scatter',
        data: {
            datasets: [
                {
                    label: 'symmetric (y = x)',
                    type: 'line',
                    data: [{x: -1, y: -1}, {x: 1, y: 1}],
                    borderColor: '#888',
                    borderDash: [4,4],
                    borderWidth: 1,
                    pointRadius: 0,
                    fill: false,
                    showLine: true,
                },
                {
                    label: 'source',
                    data: points,
                    backgroundColor: colors,
                    borderColor: '#333',
                    borderWidth: 0.3,
                    pointRadius: sizes,
                    pointHoverRadius: ctx => sizes[ctx.dataIndex] + 2,
                },
            ],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: () => '',
                        label: c => {
                            if (c.datasetIndex === 0) return '';
                            const r = points[c.dataIndex]?._row;
                            if (!r) return '';
                            return [
                                `source ${r.source_id}`,
                                `fw = ${r.id_fw.toFixed(2)} (n=${r.n_views_fw}) · bw = ${r.id_bw.toFixed(2)} (n=${r.n_views_bw})`,
                                `asymmetry = ${(r.asymmetry >= 0 ? '+' : '') + r.asymmetry.toFixed(2)}  → ${r.preferred_direction}`,
                            ];
                        },
                    },
                },
            },
            scales: {
                x: { min: -1.05, max: 1.05, title: { display: true, text: 'forward identifiability' }, grid: { color: '#eee' } },
                y: { min: -1.05, max: 1.05, title: { display: true, text: 'backward identifiability' }, grid: { color: '#eee' } },
            },
        },
    });
}

function chartIdScore() {
    destroy('id');
    const hist = PAYLOAD.per_video.id_score_hist;
    const labels = hist.map(h => h.bin_center.toFixed(2));
    const data = hist.map(h => h.count);
    charts.id = new Chart(document.getElementById('ch-id'), {
        type: 'bar',
        data: { labels, datasets: [{
            data,
            backgroundColor: data.map((_, i) => {
                const c = hist[i].bin_center;
                return c >= 0.5 ? COLORS.included + 'CC'
                     : c <= -0.5 ? COLORS.excluded + 'CC'
                     : COLORS.grey + 'CC';
            }),
            borderWidth: 0,
            borderRadius: 3,
        }]},
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => {
                            const h = hist[items[0].dataIndex];
                            return `${h.bin_lo.toFixed(2)} – ${h.bin_hi.toFixed(2)}`;
                        },
                        label: c => `n cells = ${c.parsed.y}`,
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, title: { display: true, text: 'identifiability score', font: { size: 11 } } },
                y: { beginAtZero: true, title: { display: true, text: '(stim × direction) cells' } },
            },
        },
    });
}

function chartCoverage() {
    destroy('coverage');
    const cov = PAYLOAD.per_video.coverage_at_n;
    const ns = [1,2,3,5,10,20];
    const data = ns.map(n => cov[n] || 0);
    const target = PAYLOAD.per_video.total_cells_corpus;
    const data_target = ns.map(() => target);
    charts.coverage = new Chart(document.getElementById('ch-coverage'), {
        type: 'bar',
        data: { labels: ns.map(n => `≥ ${n}`), datasets: [
            { label: 'cells with n views', data, backgroundColor: COLORS.accent + 'CC' },
            { label: 'corpus target (4400)', data: data_target, type: 'line',
              borderColor: '#111', borderDash: [4,4], borderWidth: 1.5, pointRadius: 0, fill: false },
        ]},
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: true, position: 'top', labels: { font: { size: 11 } } } },
            scales: {
                x: { grid: { display: false }, title: { display: true, text: 'minimum views per cell' } },
                y: { beginAtZero: true, title: { display: true, text: 'cells' } },
            },
        },
    });
}

function chartDuration() {
    const subs = activeSubjects();
    const edges = [0, 20, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 120];
    chartHistogram('duration', 'ch-duration',
        subs.map(s => s.total_session_min), edges,
        { barColor: COLORS.accent2, x_title: 'session duration (min)', label_decimals: 0 });
}

// ---------- table ----------
function sortSubjects(subs) {
    const dir = sortDir === 'asc' ? 1 : -1;
    return subs.slice().sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (va === null || va === undefined) va = -Infinity;
        if (vb === null || vb === undefined) vb = -Infinity;
        if (typeof va === 'string') return dir * va.localeCompare(vb);
        return dir * (va - vb);
    });
}

function renderTable() {
    const subs = sortSubjects(activeSubjects());
    const thead = document.getElementById('thead-row');
    thead.innerHTML = TABLE_COLS.map(c => {
        const arrow = sortCol === c.key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';
        return `<th data-col="${c.key}">${c.label}${arrow}</th>`;
    }).join('');
    thead.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {
        const k = th.getAttribute('data-col');
        if (sortCol === k) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        else { sortCol = k; sortDir = 'desc'; }
        renderTable();
    }));

    const tbody = document.getElementById('tbody');
    tbody.innerHTML = subs.map(s => {
        const cls = s.included ? 'included' : 'excluded';
        const hi  = s.pid_hash === highlightPid ? 'highlight' : '';
        return `<tr class="${cls} ${hi}" data-pid="${s.pid_hash}">${
            TABLE_COLS.map(c => {
                const v = s[c.key];
                const html = fmt(v, c.fmt);
                const right = ['int','f0','f2','f3','fbias'].includes(c.fmt) ? 'right' : '';
                const mono = c.key === 'pid_hash' ? 'mono' : '';
                return `<td class="${right} ${mono}">${html}</td>`;
            }).join('')
        }</tr>`;
    }).join('');
    tbody.querySelectorAll('tr').forEach(tr => tr.addEventListener('click', () => {
        const pid = tr.getAttribute('data-pid');
        highlightPid = (highlightPid === pid) ? null : pid;
        renderTable();
        chartCalibration();
        chartDrift();
    }));
}

// ---------- wire-up ----------
function renderAll() {
    renderKpis();
    chartInclusion();
    chartExclusion();
    chartDPrime();
    chartMRatio();
    chartCatch();
    chartForward();
    chartCalibration();
    chartDrift();
    chartIdScore();
    chartAsymmetry();
    chartCoverage();
    chartDuration();
    renderTable();
}

document.getElementById('filter-scope').addEventListener('change', e => {
    scope = e.target.value;
    highlightPid = null;
    renderAll();
});

renderAll();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    build_dashboard()

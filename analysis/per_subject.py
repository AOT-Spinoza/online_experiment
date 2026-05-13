"""
Per-subject summary table + diagnostic figures.

Reads responses.parquet (from analysis.load_all), computes one row per
session with engagement / gate / catch / SDT / metacognition metrics,
and writes:

  - analysis/derived/per_subject.tsv
  - analysis/derived/figures/per_subject/<pid_hash>.png   (one per session)

Metrics (column-name → meaning):

  Engagement / lapses
    total_session_ms              wall-time of the session
    n_main_real                   count of (pm_type='main', phase='main') stimulus rows
    n_main_catch                  count of (pm_type='catch', phase='main') stimulus rows
    n_blocks_completed            distinct block_index values seen with ≥ 1 stim row
    frac_missed_main              fraction with response_direction NaN
    frac_fast_lapses              fraction with direction_rt < 250 ms
    frac_play_completed           fraction of video_play rows with play_completed=True
    median_direction_rt_main
    median_confidence_rt_main

  Gates (ground truth was shipped on these phases)
    practice_direction_accuracy
    qualification_direction_accuracy

  Catch trials (the attention probe)
    catch_n
    catch_direction_pass_rate     fraction with direction matching pm_direction
    catch_confidence_pass_rate    fraction with confidence matching pm_expected_confidence
    catch_full_pass_rate          both right — this is the §3.9 inclusion gate (≥ 0.80)

  Main-task accuracy
    main_direction_accuracy       computed offline via private manifest
    forward_response_rate
    forward_bias                  = forward_response_rate − 0.5

  Type-1 SDT (discrimination ability)
    d_prime
    criterion                     positive = forward bias in SDT terms

  Type-2 SDT / metacognition
    meta_d_prime                  type-2 sensitivity (metadpy MLE)
    m_ratio                       meta_d_prime / d_prime — metacog efficiency
    calib_gamma                   Goodman-Kruskal γ between confidence and correctness
    calib_logit_slope             slope of logistic regression `correct ~ confidence`
    calib_acc_conf{1..5}          binned accuracy at each confidence level

  Composite + inclusion
    subject_quality_weight        catch_full_pass × clip(d',0,3)/3 × clip(m_ratio,0,1.5)/1.5
    included                      boolean — passes the §3.9 inclusion gate

Usage:
    from analysis.per_subject import build_per_subject_table
    df = build_per_subject_table()

    python -m analysis.per_subject
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

# Headless-safe by default; the explore notebook can override.
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats
from scipy.special import ndtri  # inverse normal CDF

from .load_all import DEFAULT_DERIVED_DIR, REPO_ROOT, short_hash


# ---------------------------------------------------------------------------
# SDT primitives
# ---------------------------------------------------------------------------

def _safe_rate(k: int, n: int) -> float:
    """Snodgrass-Corwin log-linear correction so Z(0) and Z(1) don't blow up.
    Returns (k + 0.5) / (n + 1)."""
    if n <= 0:
        return float('nan')
    return (k + 0.5) / (n + 1)


def compute_dprime(stim: pd.Series, response: pd.Series) -> tuple[float, float]:
    """Type-1 d' and criterion for a binary forward/backward task.

    Treats `forward` as signal, `backward` as noise.
      hits = forward stim → forward response
      false_alarms = backward stim → forward response

    Returns (d_prime, criterion). Criterion > 0 means forward bias.
    """
    mask = stim.notna() & response.notna()
    s = stim[mask]
    r = response[mask]
    n_fwd_stim = int((s == 'forward').sum())
    n_bwd_stim = int((s == 'backward').sum())
    n_hits = int(((s == 'forward') & (r == 'forward')).sum())
    n_fa = int(((s == 'backward') & (r == 'forward')).sum())
    if n_fwd_stim == 0 or n_bwd_stim == 0:
        return float('nan'), float('nan')
    h = _safe_rate(n_hits, n_fwd_stim)
    f = _safe_rate(n_fa, n_bwd_stim)
    d = float(ndtri(h) - ndtri(f))
    c = float(-0.5 * (ndtri(h) + ndtri(f)))
    return d, c


def compute_meta_d(stim: pd.Series, correct: pd.Series, confidence: pd.Series,
                   n_ratings: int = 5) -> tuple[float, float]:
    """Type-2 meta-d' (Maniscalco–Lau) via metadpy MLE.

    Returns (meta_d_prime, m_ratio). NaN if metadpy isn't available, the
    data isn't sufficient, or every retry of the MLE fails to converge.

    Uses metadpy.mle.metad with a DataFrame containing:
      Stimuli ∈ {0, 1}, Accuracy ∈ {0, 1}, Confidence ∈ {1..n_ratings}

    Padding: metadpy's default `padAmount` is `1/(2·nRatings) = 0.1`,
    which is too small whenever a participant has *no* trials in some
    (stimulus × response × confidence) cell — e.g. zero confidence-5
    confident-wrong responses in one direction. The MLE then divides
    by a zero Type-2 rate and fails with "division by zero". Empirically
    `padAmount=0.5` (standard Snodgrass-Corwin +0.5) resolves it; we
    fall back to 1.0 if that still fails. The bias introduced is small
    (the participant has hundreds of trials anchoring the estimate)
    relative to the alternative of returning NaN.
    """
    mask = stim.notna() & correct.notna() & confidence.notna()
    s = stim[mask]
    a = correct[mask]
    c = confidence[mask]
    if len(s) < 20:  # not enough trials for stable estimate
        return float('nan'), float('nan')
    try:
        from metadpy.mle import metad  # local import — heavy
    except Exception as e:
        warnings.warn(f"metadpy unavailable: {e}")
        return float('nan'), float('nan')

    df = pd.DataFrame({
        'Stimuli': (s == 'forward').astype(int).values,
        'Accuracy': a.astype(int).values,
        'Confidence': c.astype(int).values,
    })

    last_err: Exception | None = None
    for pad in (0.5, 1.0):
        try:
            out = metad(
                data=df,
                nRatings=n_ratings,
                stimuli='Stimuli',
                accuracy='Accuracy',
                confidence='Confidence',
                padding=True,
                padAmount=pad,
                verbose=0,
            )
        except Exception as e:
            last_err = e
            continue
        if isinstance(out, pd.DataFrame) and len(out):
            row = out.iloc[0]
            meta_d = float(row.get('meta_d', row.get('metad', float('nan'))))
            m_ratio = float(row.get('m_ratio', row.get('mratio', float('nan'))))
            return meta_d, m_ratio
    if last_err is not None:
        warnings.warn(f"metad fit failed at all padAmount values: {last_err}")
    return float('nan'), float('nan')


def compute_gamma(confidence: pd.Series, correct: pd.Series) -> float:
    """Goodman-Kruskal γ between confidence (ordinal 1..5) and correctness
    (binary 0/1). Same family as Kendall's τ-b but rescaled. Defined on
    valid data only — returns NaN if too few observations."""
    mask = confidence.notna() & correct.notna()
    c = confidence[mask].astype(int).to_numpy()
    a = correct[mask].astype(int).to_numpy()
    if len(c) < 10:
        return float('nan')
    # Count concordant / discordant pairs efficiently via Kendall.
    # γ = (C − D) / (C + D), ignoring tied pairs.
    tau, _ = stats.kendalltau(c, a, variant='c')
    # Convert τ-c to γ when one variable is binary: γ ≈ τ-c / min(p, 1-p)
    # is messy; use the direct C/D formulation instead.
    n = len(c)
    n_conc = n_disc = 0
    # O(n^2) but n ≤ ~400 → fine. Vectorise if it ever matters.
    for i in range(n):
        for j in range(i + 1, n):
            if c[i] == c[j] or a[i] == a[j]:
                continue
            if (c[i] < c[j]) == (a[i] < a[j]):
                n_conc += 1
            else:
                n_disc += 1
    if n_conc + n_disc == 0:
        return float('nan')
    return float((n_conc - n_disc) / (n_conc + n_disc))


def compute_calib_logit_slope(confidence: pd.Series, correct: pd.Series) -> float:
    """Slope of a logistic regression `correct ~ confidence`. Positive
    slope = higher confidence predicts higher accuracy."""
    mask = confidence.notna() & correct.notna()
    c = confidence[mask].astype(float).to_numpy()
    a = correct[mask].astype(int).to_numpy()
    if len(c) < 10 or len(np.unique(c)) < 2 or len(np.unique(a)) < 2:
        return float('nan')
    # No statsmodels dependency — use scipy via Newton on the logit MLE.
    # Equivalent to logistic regression with one predictor.
    try:
        from sklearn.linear_model import LogisticRegression
        # Effectively unpenalised: C → ∞ is sklearn ≥ 1.8's replacement
        # for the deprecated `penalty=None` keyword.
        lr = LogisticRegression(C=1e12, solver='lbfgs', max_iter=200)
        lr.fit(c.reshape(-1, 1), a)
        return float(lr.coef_[0, 0])
    except Exception:
        # Fallback: closed-form via stats.linregress on the logit of
        # binned accuracy. Less accurate but doesn't pull in sklearn
        # if it's not installed.
        bins = pd.Series(a).groupby(pd.Series(c)).mean()
        if len(bins) < 2:
            return float('nan')
        # Add a small offset to avoid logit(0) / logit(1).
        eps = 1e-3
        y = np.clip(bins.to_numpy(), eps, 1 - eps)
        logits = np.log(y / (1 - y))
        slope, _, _, _, _ = stats.linregress(bins.index.to_numpy(), logits)
        return float(slope)


# ---------------------------------------------------------------------------
# Per-session metric assembly
# ---------------------------------------------------------------------------

def _session_metrics(sess_df: pd.DataFrame) -> dict:
    """Compute every per-session metric defined in the module docstring."""
    pid = sess_df['pid'].iloc[0]
    pid_hash = sess_df['pid_hash'].iloc[0] if 'pid_hash' in sess_df.columns else short_hash(pid)
    ts_ms = sess_df['session_start_ms'].dropna().iloc[0] if (
        'session_start_ms' in sess_df.columns and sess_df['session_start_ms'].notna().any()
    ) else None
    iso = (
        pd.to_datetime(ts_ms, unit='ms', utc=True).isoformat()
        if ts_ms is not None and pd.notna(ts_ms) else ''
    )
    total_session_ms = float(pd.to_numeric(sess_df['time_elapsed'], errors='coerce').max()) \
        if 'time_elapsed' in sess_df.columns else float('nan')

    # Restrict to canonical 'stimulus' rows for response-level analysis.
    stim = sess_df[sess_df['trial_type_tag'] == 'stimulus'].copy()

    main = stim[stim['phase'] == 'main']
    main_real = main[main['pm_type'] == 'main']
    main_catch = main[main['pm_type'] == 'catch']
    practice = stim[stim['phase'] == 'practice']
    qualification = stim[stim['phase'] == 'qualification']

    n_main_real = len(main_real)
    n_main_catch = len(main_catch)
    n_blocks = int(main['block_index'].dropna().nunique()) if len(main) else 0

    # Engagement / lapses computed on main only.
    main_with_resp = main[main['response_direction'].notna()]
    frac_missed_main = (1 - len(main_with_resp) / max(1, len(main))) if len(main) else float('nan')

    dirrt = pd.to_numeric(main['direction_rt'], errors='coerce')
    conf_rt = pd.to_numeric(main['confidence_rt'], errors='coerce')
    frac_fast_lapses = (dirrt < 250).sum() / max(1, len(main)) if len(main) else float('nan')
    median_dir_rt = float(dirrt.median()) if dirrt.notna().any() else float('nan')
    median_conf_rt = float(conf_rt.median()) if conf_rt.notna().any() else float('nan')

    # play_completed lives on video_play rows.
    vp = sess_df[sess_df['trial_type_tag'] == 'video_play']
    if len(vp) and 'play_completed' in vp.columns:
        # Boolean comes back from parquet as bool / 'True'/'False' / NaN — coerce.
        pc = vp['play_completed'].map(
            lambda x: True if x is True or str(x).lower() == 'true'
            else (False if x is False or str(x).lower() == 'false' else None),
        )
        frac_play_completed = float(pc.dropna().mean()) if pc.notna().any() else float('nan')
    else:
        frac_play_completed = float('nan')

    # Practice / qualification accuracy — `correct` column was set by the
    # runtime because ground truth shipped for these phases.
    def _acc(df, col='correct'):
        if col not in df.columns or not len(df):
            return float('nan')
        v = df[col]
        # Coerce 'True'/'False'/True/False/1/0 → bool
        v = v.map(lambda x: True if (x is True or x == 1 or str(x).lower() == 'true')
                  else (False if (x is False or x == 0 or str(x).lower() == 'false') else None))
        v = v.dropna()
        return float(v.mean()) if len(v) else float('nan')

    practice_acc = _acc(practice)
    qual_acc = _acc(qualification)

    # Catch — offline scoring via pm_direction + pm_expected_confidence.
    if len(main_catch):
        catch_dir = (main_catch['response_direction'] == main_catch['pm_direction'])
        catch_conf = (
            pd.to_numeric(main_catch['confidence'], errors='coerce')
            == pd.to_numeric(main_catch['pm_expected_confidence'], errors='coerce')
        )
        catch_full = catch_dir & catch_conf
        catch_n = len(main_catch)
        catch_dir_rate = float(catch_dir.mean())
        catch_conf_rate = float(catch_conf.mean())
        catch_full_rate = float(catch_full.mean())
    else:
        catch_n = 0
        catch_dir_rate = catch_conf_rate = catch_full_rate = float('nan')

    # Main accuracy via private manifest (main_correct already joined).
    if len(main_real) and 'main_correct' in main_real.columns:
        mc = main_real['main_correct'].dropna()
        # main_correct is a nullable boolean — convert to int.
        if hasattr(mc, 'astype'):
            mc_int = mc.astype('boolean').astype('Int64').dropna().astype(int)
        else:
            mc_int = mc.astype(int)
        main_acc = float(mc_int.mean()) if len(mc_int) else float('nan')
    else:
        main_acc = float('nan')

    forward_response_rate = (
        (main_real['response_direction'] == 'forward').mean()
        if len(main_real) else float('nan')
    )
    forward_bias = (forward_response_rate - 0.5) if pd.notna(forward_response_rate) else float('nan')

    # SDT pair on main real trials.
    d_prime, criterion = compute_dprime(
        main_real['pm_direction'], main_real['response_direction'],
    ) if len(main_real) else (float('nan'), float('nan'))

    # Confidence + correctness for type-2.
    main_real_conf = pd.to_numeric(main_real['confidence'], errors='coerce')
    if len(main_real) and 'main_correct' in main_real.columns:
        mc_bool = main_real['main_correct'].astype('boolean')
        meta_d, m_ratio = compute_meta_d(
            main_real['pm_direction'],
            mc_bool.astype('Int64').astype('float').dropna(),
            main_real_conf,
        )
    else:
        meta_d = m_ratio = float('nan')

    # Calibration γ and logistic slope.
    if len(main_real) and 'main_correct' in main_real.columns:
        mc_int = main_real['main_correct'].astype('boolean').astype('Int64').astype('float')
        calib_gamma = compute_gamma(main_real_conf, mc_int)
        calib_slope = compute_calib_logit_slope(main_real_conf, mc_int)
    else:
        calib_gamma = calib_slope = float('nan')

    # Per-confidence-level binned accuracy.
    calib_bins = {f'calib_acc_conf{i}': float('nan') for i in range(1, 6)}
    calib_n_bins = {f'calib_n_conf{i}': 0 for i in range(1, 6)}
    if len(main_real) and 'main_correct' in main_real.columns:
        mc_bool = main_real['main_correct'].astype('boolean').astype('Int64')
        for level in range(1, 6):
            in_bin = main_real_conf == level
            calib_n_bins[f'calib_n_conf{level}'] = int(in_bin.sum())
            if in_bin.any():
                bin_acc = mc_bool[in_bin].astype('float').mean()
                calib_bins[f'calib_acc_conf{level}'] = float(bin_acc)

    # Composite quality weight.
    #
    #   weight = catch_full_pass × ability × metacog
    #
    # Each factor maps to [0, 1] with cap; product zeroes out a
    # subject who fails any one. `metacog` prefers M-ratio (the
    # principled SDT metric); falls back to Goodman–Kruskal γ when
    # the meta-d' MLE didn't converge (γ ∈ [−1, +1] → scale by 1.5
    # so γ = 2/3 maps to a max-credit weight contribution, matching
    # the m_ratio = 1.0 cap). If neither is available we conservatively
    # weight as zero.
    def _clip01(x, lo=0.0, hi=1.0):
        return float(np.clip(x, lo, hi)) if pd.notna(x) else 0.0

    cap_d = _clip01(d_prime / 3.0) if pd.notna(d_prime) else 0.0
    cap_catch = _clip01(catch_full_rate) if pd.notna(catch_full_rate) else 0.0
    if pd.notna(m_ratio):
        cap_m = _clip01(m_ratio / 1.5)
        metacog_source = 'm_ratio'
    elif pd.notna(calib_gamma):
        cap_m = _clip01(calib_gamma * 1.5)
        metacog_source = 'calib_gamma_fallback'
    else:
        cap_m = 0.0
        metacog_source = 'none'
    subject_quality_weight = cap_catch * cap_d * cap_m

    # Hard inclusion gate from §3.9.
    included = bool(
        (catch_full_rate >= 0.80 if pd.notna(catch_full_rate) else False)
        and (qual_acc >= 0.75 if pd.notna(qual_acc) else False)
        and (250 <= median_dir_rt <= 3000 if pd.notna(median_dir_rt) else False)
        and (calib_gamma > 0 if pd.notna(calib_gamma) else False)
        and (frac_play_completed >= 0.9 if pd.notna(frac_play_completed) else False)
    )

    return {
        'pid': pid,
        'pid_hash': pid_hash,
        'session_start_ms': int(ts_ms) if ts_ms is not None and pd.notna(ts_ms) else None,
        'session_start_iso': iso,
        'total_session_ms': total_session_ms,
        'n_main_real': n_main_real,
        'n_main_catch': n_main_catch,
        'n_blocks_completed': n_blocks,
        'frac_missed_main': frac_missed_main,
        'frac_fast_lapses': frac_fast_lapses,
        'frac_play_completed': frac_play_completed,
        'median_direction_rt_main': median_dir_rt,
        'median_confidence_rt_main': median_conf_rt,
        'practice_direction_accuracy': practice_acc,
        'qualification_direction_accuracy': qual_acc,
        'catch_n': catch_n,
        'catch_direction_pass_rate': catch_dir_rate,
        'catch_confidence_pass_rate': catch_conf_rate,
        'catch_full_pass_rate': catch_full_rate,
        'main_direction_accuracy': main_acc,
        'forward_response_rate': forward_response_rate,
        'forward_bias': forward_bias,
        'd_prime': d_prime,
        'criterion': criterion,
        'meta_d_prime': meta_d,
        'm_ratio': m_ratio,
        'calib_gamma': calib_gamma,
        'calib_logit_slope': calib_slope,
        **calib_bins,
        **calib_n_bins,
        'subject_quality_weight': subject_quality_weight,
        'metacog_source': metacog_source,
        'included': included,
    }


# ---------------------------------------------------------------------------
# Per-subject diagnostic figure
# ---------------------------------------------------------------------------

def make_subject_figure(sess_df: pd.DataFrame, metrics: dict, out_path: Path) -> None:
    """One PNG per session: calibration curve, RT histograms, conf hist,
    forward bias bar, and a text panel listing the headline numbers."""
    stim = sess_df[sess_df['trial_type_tag'] == 'stimulus']
    main_real = stim[(stim['phase'] == 'main') & (stim['pm_type'] == 'main')].copy()
    main_real['confidence'] = pd.to_numeric(main_real['confidence'], errors='coerce')
    main_real['direction_rt'] = pd.to_numeric(main_real['direction_rt'], errors='coerce')

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    fig.suptitle(
        f"Subject {metrics['pid_hash']}  |  "
        f"d'={metrics['d_prime']:.2f}  meta-d'={metrics['meta_d_prime']:.2f}  "
        f"M-ratio={metrics['m_ratio']:.2f}  catch={metrics['catch_full_pass_rate']:.2f}  "
        f"weight={metrics['subject_quality_weight']:.2f}  "
        f"{'INCLUDED' if metrics['included'] else 'EXCLUDED'}",
        fontsize=12,
    )

    # (0,0) Calibration curve: accuracy by confidence
    ax = axes[0, 0]
    levels = list(range(1, 6))
    accs = [metrics.get(f'calib_acc_conf{i}', float('nan')) for i in levels]
    ns = [metrics.get(f'calib_n_conf{i}', 0) for i in levels]
    ax.bar(levels, accs, color='#2a6e8c', alpha=0.85)
    ax.axhline(0.5, color='red', ls='--', lw=1, label='chance')
    for x, a, n in zip(levels, accs, ns):
        if pd.notna(a):
            ax.text(x, a + 0.02, f'n={n}', ha='center', fontsize=9)
    ax.set_xlabel('confidence rating')
    ax.set_ylabel('direction accuracy')
    ax.set_ylim(0, 1.1)
    ax.set_title(f'calibration  (γ={metrics["calib_gamma"]:.2f})')
    ax.legend(loc='lower right', fontsize=9)

    # (0,1) Direction RT histogram, log-x.
    ax = axes[0, 1]
    rts = main_real['direction_rt'].dropna()
    if len(rts):
        ax.hist(rts, bins=30, color='#5a7d4e', alpha=0.85)
        ax.axvline(rts.median(), color='red', ls='--', lw=1,
                   label=f'median = {rts.median():.0f} ms')
        ax.axvline(250, color='orange', ls=':', lw=1, label='250 ms lapse cutoff')
        ax.legend(fontsize=9)
    ax.set_xlabel('direction RT (ms)')
    ax.set_ylabel('count')
    ax.set_title('main-task direction RT')

    # (0,2) Confidence histogram split by correctness
    ax = axes[0, 2]
    if 'main_correct' in main_real.columns:
        for label, c, col in [('correct', True, '#2a8c2a'),
                              ('wrong', False, '#c43b3b')]:
            mask = main_real['main_correct'].astype('boolean') == c
            v = main_real.loc[mask, 'confidence'].dropna()
            if len(v):
                ax.hist(v, bins=np.arange(0.5, 6.5, 1), alpha=0.6,
                        label=f'{label} (n={len(v)})', color=col)
        ax.legend(fontsize=9)
    ax.set_xlabel('confidence')
    ax.set_ylabel('count')
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_title('confidence by correctness')

    # (1,0) Forward-response rate broken down by ground-truth direction
    ax = axes[1, 0]
    if 'pm_direction' in main_real.columns:
        g = (
            main_real.groupby('pm_direction')['response_direction']
            .apply(lambda s: (s == 'forward').mean())
            .reindex(['forward', 'backward'])
        )
        ax.bar(g.index, g.values, color=['#2a6e8c', '#8c4a2a'])
        ax.axhline(0.5, color='red', ls='--', lw=1)
        for i, (idx, v) in enumerate(g.items()):
            if pd.notna(v):
                ax.text(i, v + 0.02, f'{v:.2f}', ha='center', fontsize=10)
    ax.set_ylim(0, 1.1)
    ax.set_ylabel('P(say forward)')
    ax.set_title(f'forward-bias  (bias={metrics["forward_bias"]:+.2f})')

    # (1,1) Trial-by-trial sliding accuracy
    ax = axes[1, 1]
    if 'main_correct' in main_real.columns and len(main_real):
        ordered = main_real.sort_values(['block_index', 'trial_index_in_block']).reset_index(drop=True)
        acc = ordered['main_correct'].astype('boolean').astype('Int64').astype(float)
        if len(acc) > 10:
            window = min(20, max(5, len(acc) // 10))
            roll = acc.rolling(window, min_periods=window // 2, center=True).mean()
            ax.plot(roll.index, roll.values, color='#2a6e8c', lw=1.5,
                    label=f'rolling mean, w={window}')
            ax.axhline(acc.mean(), color='black', ls=':', lw=1,
                       label=f'overall = {acc.mean():.2f}')
            ax.set_ylim(0, 1.05)
            ax.legend(fontsize=9, loc='lower right')
    ax.set_xlabel('trial index (main, ordered)')
    ax.set_ylabel('accuracy')
    ax.set_title('drift check')

    # (1,2) Headline numbers as text
    ax = axes[1, 2]
    ax.axis('off')
    lines = [
        f"PID hash:               {metrics['pid_hash']}",
        f"session start:           {metrics['session_start_iso'][:19]}",
        f"session ms:              {metrics['total_session_ms']:.0f}",
        f"blocks completed:        {metrics['n_blocks_completed']}",
        f"n_main_real / catch:     {metrics['n_main_real']} / {metrics['n_main_catch']}",
        f"frac missed (main):      {metrics['frac_missed_main']:.3f}",
        f"frac fast lapses:        {metrics['frac_fast_lapses']:.3f}",
        f"frac play_completed:     {metrics['frac_play_completed']:.3f}",
        '',
        f"practice acc:            {metrics['practice_direction_accuracy']:.3f}",
        f"qualification acc:       {metrics['qualification_direction_accuracy']:.3f}",
        f"catch direction:         {metrics['catch_direction_pass_rate']:.3f}",
        f"catch confidence:        {metrics['catch_confidence_pass_rate']:.3f}",
        f"catch full pass:         {metrics['catch_full_pass_rate']:.3f}",
        '',
        f"main accuracy:           {metrics['main_direction_accuracy']:.3f}",
        f"d':                      {metrics['d_prime']:.3f}",
        f"criterion:               {metrics['criterion']:+.3f}",
        f"meta-d':                 {metrics['meta_d_prime']:.3f}",
        f"M-ratio:                 {metrics['m_ratio']:.3f}",
        f"calib γ:                 {metrics['calib_gamma']:.3f}",
        '',
        f"quality weight:          {metrics['subject_quality_weight']:.3f}",
        f"INCLUDED:                {metrics['included']}",
    ]
    ax.text(0.0, 1.0, '\n'.join(lines), family='monospace', fontsize=10,
            verticalalignment='top', transform=ax.transAxes)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def build_per_subject_table(
    responses_path: str | Path | None = None,
    out_tsv: str | Path | None = None,
    figures_dir: str | Path | None = None,
    make_figures: bool = True,
) -> pd.DataFrame:
    """Aggregate per-session metrics, write TSV, optionally write PNGs."""
    if responses_path is None:
        responses_path = DEFAULT_DERIVED_DIR / 'responses.parquet'
    if out_tsv is None:
        out_tsv = DEFAULT_DERIVED_DIR / 'per_subject.tsv'
    if figures_dir is None:
        figures_dir = DEFAULT_DERIVED_DIR / 'figures' / 'per_subject'

    responses_path = Path(responses_path)
    out_tsv = Path(out_tsv)
    figures_dir = Path(figures_dir)
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    if make_figures:
        figures_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(responses_path)
    print(f"[per_subject] loaded {len(df):,} rows from {responses_path}")

    rows = []
    sessions = df.groupby('pid_session_id', sort=False)
    for sess_id, sess_df in sessions:
        m = _session_metrics(sess_df)
        m['pid_session_id'] = sess_id
        rows.append(m)
        if make_figures:
            try:
                make_subject_figure(
                    sess_df, m, figures_dir / f"{m['pid_hash']}.png",
                )
            except Exception as e:
                warnings.warn(f"figure failed for {m['pid_hash']}: {e}")

    out = pd.DataFrame(rows)
    # Tidy column order: identity, then engagement, then gates, then SDT,
    # then composite, then per-bin breakdown.
    front = ['pid', 'pid_hash', 'pid_session_id', 'session_start_iso', 'included',
             'subject_quality_weight', 'd_prime', 'meta_d_prime', 'm_ratio',
             'catch_full_pass_rate', 'main_direction_accuracy']
    front = [c for c in front if c in out.columns]
    rest = [c for c in out.columns if c not in front]
    out = out[front + rest]

    out.to_csv(out_tsv, sep='\t', index=False, float_format='%.4f')
    print(f"[per_subject] wrote {len(out)} session(s) to {out_tsv}")
    if make_figures:
        print(f"[per_subject] PNGs under {figures_dir}/")
    return out


if __name__ == '__main__':
    build_per_subject_table()

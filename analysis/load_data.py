"""
Reusable loaders for analysing AoT experiment data.

Two input shapes are supported:

  1. The JSON bundle produced by the "Download saved data" button on the
     local-dev end page. Schema is { 'aot_<pid>_<suffix>': '<csv text>', ... }
     where each value is the cumulative jsPsych data CSV at the moment that
     suffix's save trial ran.

  2. A bare CSV file as written to OSF/DataPipe in production (one
     participant's data, all rows).

Loaders normalise to a single pandas DataFrame with columns roughly
matching the per-trial logged fields documented in CLAUDE.md §3.7.

A second helper joins the per-trial DataFrame with the private manifest
(secrets/manifest_private.json) so main-task correctness, catch-trial
expected responses, etc. are restored at analysis time. The private
manifest never ships with the experiment — it lives only on the analyst's
machine — so the join happens here, post-hoc.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


_KEY_RE = re.compile(r'^aot_(.+?)_(final|block\d+)$')


def list_sessions(path: str | Path) -> pd.DataFrame:
    """List every session present in a JSON bundle.

    Local dev mode keeps every session you've ever run in localStorage,
    so a single export usually contains multiple sessions. This returns
    them with their start timestamp so you can pick the one you want.

    Columns: pid, session_start_ms, session_start_iso, n_rows_final,
    has_block_saves. Sorted most-recent-first.
    """
    p = Path(path)
    if p.suffix.lower() != '.json':
        return pd.DataFrame(columns=['pid', 'session_start_ms', 'session_start_iso',
                                     'n_rows_final', 'has_block_saves'])
    with p.open() as f:
        bundle = json.load(f)

    grouped: dict[str, dict] = {}
    for key, csv_text in bundle.items():
        m = _KEY_RE.match(key)
        if not m:
            continue
        pid, suffix = m.group(1), m.group(2)
        grouped.setdefault(pid, {'pid': pid, 'suffixes': set()})
        grouped[pid]['suffixes'].add(suffix)
        if suffix == 'final':
            grouped[pid]['final_csv'] = csv_text

    rows = []
    for pid, info in grouped.items():
        csv_text = info.get('final_csv', '')
        if not csv_text:
            continue
        df = pd.read_csv(io.StringIO(csv_text))
        ts_ms = None
        if 'session_start_ms' in df.columns:
            valid = df['session_start_ms'].dropna()
            if len(valid):
                ts_ms = int(valid.iloc[0])
        rows.append({
            'pid': pid,
            'session_start_ms': ts_ms,
            'session_start_iso': (
                datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                if ts_ms is not None else None
            ),
            'n_rows_final': len(df),
            'has_block_saves': any(s.startswith('block') for s in info['suffixes']),
        })
    out = pd.DataFrame(rows)
    if len(out):
        # Most recent first; sessions without a timestamp (legacy data) sink
        # to the bottom but are still listed.
        out = out.sort_values('session_start_ms', ascending=False, na_position='last').reset_index(drop=True)
    return out


def load_session(
    path: str | Path,
    session_pid: str | None = None,
) -> pd.DataFrame:
    """Load one participant's data from either a JSON bundle (local-dev
    export) or a bare CSV (production save).

    Returns a per-trial DataFrame. For JSON bundles:
      - If `session_pid` is given, loads that session's `_final` entry.
      - Otherwise picks the most recent session (largest session_start_ms),
        falling back to the last `_final` in iteration order if no
        timestamps are present (legacy data).

    Also merges confidence values from `confidence` rows onto their sibling
    `stimulus` rows, so a single stimulus row carries everything you need
    for analysis: direction response, RT, confidence, confidence RT.
    """
    p = Path(path)
    if p.suffix.lower() == '.json':
        df = _load_from_json_bundle(p, session_pid=session_pid)
    elif p.suffix.lower() == '.csv':
        df = pd.read_csv(p)
    else:
        raise ValueError(f"unsupported extension: {p.suffix} (expected .json or .csv)")
    return merge_confidence_into_stimulus(df)


def merge_confidence_into_stimulus(df: pd.DataFrame) -> pd.DataFrame:
    """Pull confidence values from `confidence` rows onto their sibling
    `stimulus` rows.

    The runtime emits separate rows per sub-trial. For a video trial that's:
      [video_play row] -> [stimulus row: direction response] -> [confidence row]
    They share (phase, block_index, trial_index_in_block) within a session.
    Without this merge, an analyst querying `trial_type_tag=='stimulus'`
    sees NaN confidence and catch-trial scoring fails — which is the bug
    we hit on the first real test.

    Confidence-ONLY familiarization trials (Layer A standalone, e.g.
    "Press 3") are tagged `trial_type_tag=='stimulus'` themselves and the
    runtime already writes confidence onto them — those rows are left
    alone.
    """
    if 'trial_type_tag' not in df.columns:
        return df

    conf_rows = df[df['trial_type_tag'] == 'confidence']
    if len(conf_rows) == 0:
        return df

    keys = ['phase', 'block_index', 'trial_index_in_block']
    if not all(k in df.columns for k in keys):
        return df

    # Build a (phase, block_index, trial_index_in_block) → (confidence,
    # confidence_rt) lookup from the confidence rows. Drop duplicates
    # defensively in case a session somehow had two confidence rows for
    # one trial — keep the first (which is the actually-recorded one).
    lookup = (
        conf_rows[keys + ['confidence', 'confidence_rt']]
        .drop_duplicates(keys, keep='first')
        .rename(columns={'confidence': '_conf_merge', 'confidence_rt': '_conf_rt_merge'})
    )

    out = df.merge(lookup, on=keys, how='left')

    # Fill confidence/confidence_rt only on stimulus rows that don't
    # already carry a value (so confidence-only Layer A standalone rows
    # are left intact).
    stim_mask = (out['trial_type_tag'] == 'stimulus')
    if 'confidence' not in out.columns:
        out['confidence'] = pd.NA
    if 'confidence_rt' not in out.columns:
        out['confidence_rt'] = pd.NA

    needs_conf = stim_mask & out['confidence'].isna()
    out.loc[needs_conf, 'confidence'] = out.loc[needs_conf, '_conf_merge']
    needs_conf_rt = stim_mask & out['confidence_rt'].isna()
    out.loc[needs_conf_rt, 'confidence_rt'] = out.loc[needs_conf_rt, '_conf_rt_merge']

    out = out.drop(columns=['_conf_merge', '_conf_rt_merge'])
    # Preserve attrs that read_csv-style loaders don't carry through merges.
    out.attrs.update(df.attrs)
    return out


def _load_from_json_bundle(path: Path, session_pid: str | None = None) -> pd.DataFrame:
    with path.open() as f:
        bundle = json.load(f)

    if not bundle:
        raise ValueError(f"empty bundle: {path}")

    if session_pid is not None:
        chosen_key = f'aot_{session_pid}_final'
        if chosen_key not in bundle:
            raise KeyError(
                f"no '_final' entry for PID {session_pid} in {path}. "
                f"Available PIDs: {sorted(_pids_in_bundle(bundle))}",
            )
    else:
        # Prefer the most recent session by session_start_ms. Fall back to
        # the LAST '_final' in iteration order (best heuristic for legacy
        # data without a timestamp). Avoid the `final_keys[0]` pick that
        # the original loader did — that picks the FIRST session in
        # localStorage iteration order, which is usually the OLDEST.
        sessions = list_sessions(path)
        if len(sessions) and sessions['session_start_ms'].notna().any():
            chosen_pid = sessions.iloc[0]['pid']
            chosen_key = f'aot_{chosen_pid}_final'
        else:
            final_keys = [k for k in bundle if k.endswith('_final')]
            if final_keys:
                chosen_key = final_keys[-1]
            else:
                chosen_key = max(bundle.keys(), key=lambda k: len(bundle[k]))

    csv_text = bundle[chosen_key]
    df = pd.read_csv(io.StringIO(csv_text))
    df.attrs['source_file'] = str(path)
    df.attrs['chosen_key'] = chosen_key
    df.attrs['available_keys'] = sorted(bundle.keys())
    return df


def _pids_in_bundle(bundle: dict) -> set[str]:
    pids = set()
    for k in bundle:
        m = _KEY_RE.match(k)
        if m:
            pids.add(m.group(1))
    return pids


def load_private_manifest(path: str | Path) -> pd.DataFrame:
    """Load the private manifest as a DataFrame keyed by stimulus_id.

    Columns: stimulus_id, source_file, type, direction, expected_confidence
    (the last is NaN for non-catch entries).
    """
    p = Path(path)
    with p.open() as f:
        records = json.load(f)
    return pd.DataFrame(records).set_index('stimulus_id')


def join_with_private(trials: pd.DataFrame, private: pd.DataFrame) -> pd.DataFrame:
    """Left-join the per-trial data against the private manifest.

    Adds: pm_type, pm_direction, pm_expected_confidence — prefixed to avoid
    clashing with any direction/correct columns the runtime already wrote
    (those are present for practice/qualification, absent for main).

    Computes derived columns on main-task rows:
      - main_correct: response_direction == pm_direction
      - catch_passed: catch trial responded with correct direction AND
                       confidence == pm_expected_confidence
    """
    if 'stimulus_id' not in trials.columns:
        raise KeyError("trials DataFrame has no `stimulus_id` column")

    # Bring private fields in with a prefix.
    pm = private.rename(columns={
        'type': 'pm_type',
        'direction': 'pm_direction',
        'expected_confidence': 'pm_expected_confidence',
        'source_file': 'pm_source_file',
    })
    out = trials.join(pm, on='stimulus_id', how='left')

    # Compute derived correctness columns. Only meaningful where
    # response_direction is set (i.e. the canonical 'stimulus' rows for
    # video trials). Use pandas' nullable boolean dtype so we can mark
    # rows where the value is undefined (non-main / non-catch) as NA
    # without the bool-vs-object dtype FutureWarning.
    if 'response_direction' in out.columns:
        main_correct = (
            (out['pm_type'] == 'main')
            & (out['response_direction'] == out['pm_direction'])
        ).astype('boolean')
        main_correct[out['pm_type'] != 'main'] = pd.NA
        out['main_correct'] = main_correct

    if 'confidence' in out.columns and 'pm_expected_confidence' in out.columns:
        is_catch = out['pm_type'] == 'catch'
        dir_ok = out['response_direction'] == out['pm_direction']
        conf_ok = out['confidence'] == out['pm_expected_confidence']
        catch_passed = (is_catch & dir_ok & conf_ok).astype('boolean')
        catch_passed[~is_catch] = pd.NA
        out['catch_passed'] = catch_passed

    return out

// Data saving: DataPipe in production, localStorage fallback in dev.
//
// CLAUDE.md §3.8: save partial data after every main block, not only at the
// end, so a network blip late in the session doesn't lose everything. We
// produce per-block CSV uploads with distinct filenames; analysis joins
// them by PROLIFIC_PID.
//
// When DATAPIPE_EXPERIMENT_ID is still the placeholder string (i.e. the
// researcher hasn't set up a DataPipe project yet) we silently fall back to
// writing the same CSV to localStorage and to the console. This means the
// experiment runs end-to-end in `npm run dev` without any external service.

import jsPsychPipe from '@jspsych-contrib/plugin-pipe';
import CallFunction from '@jspsych/plugin-call-function';

import { DATAPIPE_EXPERIMENT_ID } from './config.js';

const PLACEHOLDER_ID = 'XXXXXXXXXXXX';

function isDataPipeConfigured() {
  return DATAPIPE_EXPERIMENT_ID && DATAPIPE_EXPERIMENT_ID !== PLACEHOLDER_ID;
}

/** Bundle every aot_* key in localStorage and trigger a browser download.
 *  Used by the local-dev end page's "Download saved data" button. Exposed
 *  on `window.aotExport` so an inline `onclick` can find it after the
 *  page body is replaced by abortExperiment(). */
function exportLocalStorageData() {
  const bundle = {};
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith('aot_')) bundle[key] = localStorage.getItem(key);
  }
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
  const a = document.createElement('a');
  a.href = url;
  a.download = `aot_data_${stamp}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
if (typeof window !== 'undefined') {
  window.aotExport = exportLocalStorageData;
}

/** Write the current jsPsych data store to localStorage as a CSV blob.
 *  Best-effort: storage may be unavailable in some browser modes (incognito
 *  with quota 0); we log and move on. */
function saveToLocalStorage(jsPsych, pid, suffix) {
  try {
    const csv = jsPsych.data.get().csv();
    const key = `aot_${pid}_${suffix}`;
    localStorage.setItem(key, csv);
    // eslint-disable-next-line no-console
    console.info(
      `[data.js] localStorage["${key}"] ← ${csv.length.toLocaleString()} bytes ` +
      `(${jsPsych.data.get().count()} rows)`,
    );
    return true;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn(`[data.js] localStorage save failed (${suffix}):`, e);
    return false;
  }
}

/** Build a save trial. Always also writes to localStorage as a backup,
 *  regardless of whether DataPipe is configured.
 *
 *  @param jsPsych - the jsPsych instance
 *  @param pid     - PROLIFIC_PID (or the LOCAL_xxx fallback) — used to
 *                   compose the filename so per-participant files don't
 *                   collide on the OSF side.
 *  @param suffix  - distinguishes the save event, e.g. "block0", "block3",
 *                   "final". Becomes part of the filename.
 *  @returns       - a jsPsych trial node (or sub-timeline) suitable for
 *                   inclusion in the master timeline.
 */
export function makeSaveTrial(jsPsych, pid, suffix) {
  const filename = `${pid}_${suffix}.csv`;

  // Always run a localStorage backup right before the network save attempt.
  const localBackup = {
    type: CallFunction,
    func: () => saveToLocalStorage(jsPsych, pid, suffix),
    data: { trial_type_tag: 'save_local', save_suffix: suffix },
  };

  if (!isDataPipeConfigured()) {
    // Pure local mode — no network call.
    return {
      timeline: [
        localBackup,
        {
          type: CallFunction,
          func: () => {
            // eslint-disable-next-line no-console
            console.info(
              `[data.js] DataPipe not configured (DATAPIPE_EXPERIMENT_ID is the ` +
              `placeholder); skipping network save for "${filename}".`,
            );
          },
          data: { trial_type_tag: 'save_skipped', save_suffix: suffix },
        },
      ],
    };
  }

  return {
    timeline: [
      localBackup,
      {
        type: jsPsychPipe,
        action: 'save',
        experiment_id: DATAPIPE_EXPERIMENT_ID,
        filename,
        data_string: () => jsPsych.data.get().csv(),
        data: { trial_type_tag: 'save_remote', save_suffix: suffix },
      },
    ],
  };
}

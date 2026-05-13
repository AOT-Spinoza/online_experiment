// Prolific URL-param capture + end-of-session handoff.
//
// Production deployment path is JATOS: JATOS owns the Prolific
// completion code (kept off the client so it can't be extracted from
// the JS bundle) and runs the Prolific redirect itself when we call
// `jatos.endStudy()`. This file therefore has no Prolific-redirect
// logic of its own — that would race with JATOS's normal ending
// procedure and risk leaving the study marked as incomplete or losing
// the final data submission.
//
// Two responsibilities remain:
//
//   1. **Capture PROLIFIC_PID, STUDY_ID, SESSION_ID from the landing
//      URL** and attach them to jsPsych's global data so every saved
//      row carries them. In the Prolific dashboard the JATOS study
//      URL is configured as e.g.:
//        https://jatos.example.edu/publix/.../start?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
//      Prolific replaces those placeholders before opening the page,
//      JATOS proxies the URL params through to the experiment HTML.
//
//   2. **End the session**: under JATOS, hand control over to JATOS
//      (which submits the final data and redirects to Prolific);
//      locally, show a debug-friendly page summarising what was
//      captured. In both cases also halt the jsPsych timeline via
//      `abortExperiment` so any queued trials don't render.

import { END_REASONS } from './config.js';
import { isUnderJatos } from './jatos_helper.js';
import { getCleanCsv } from './data.js';

const PROLIFIC_PARAMS = ['PROLIFIC_PID', 'STUDY_ID', 'SESSION_ID'];

/** Read URL search params relevant to Prolific. Missing keys are absent
 *  in the returned object, not `null`.
 *
 *  Under JATOS we MUST consult `jatos.urlQueryParameters` rather than
 *  `window.location.search`. JATOS routes the original Prolific URL
 *  (`/publix/<batch>?PROLIFIC_PID=…`) through its own publix layer and
 *  serves the experiment HTML from a different URL that does NOT carry
 *  the original query string. The PROLIFIC_PID is then only accessible
 *  via `jatos.urlQueryParameters.PROLIFIC_PID`. Reading
 *  `window.location.search` (which is what jsPsych.data.getURLVariable
 *  does) returns nothing, the recordProlificParams fallback fires, and
 *  every row gets stamped with a `LOCAL_<rand>` PID instead of the
 *  participant's real Prolific ID — which is exactly what a student
 *  reported on their first Prolific-launched JATOS pilot.
 *
 *  Outside JATOS (local dev, GitHub-Pages direct deploy, etc.) we still
 *  fall through to jsPsych's URL reader. */
export function readProlificParams(jsPsych) {
  const out = {};
  const jatosParams = (
    typeof window !== 'undefined'
    && window.jatos
    && window.jatos.urlQueryParameters
  ) ? window.jatos.urlQueryParameters : null;

  for (const key of PROLIFIC_PARAMS) {
    let v = jatosParams ? jatosParams[key] : null;
    if (!v) v = jsPsych.data.getURLVariable(key);
    if (v) out[key] = v;
  }
  return out;
}

// Tracks the params we wrote, so the local-dev end page can show what was
// captured without depending on a `getAllProperties` API that v8 does not
// expose publicly.
let _capturedParams = null;

/** Attach Prolific IDs to every row of saved data. When run outside Prolific
 *  (e.g., local `npm run dev`), generates a `LOCAL_xxx` PID so the data still
 *  has a non-null PROLIFIC_PID — analysis can filter on that prefix.
 *  Also records `session_start_ms` so the analysis loader can disambiguate
 *  multiple sessions stored in the same localStorage / DataPipe folder.
 *  Returns the params actually stored. */
export function recordProlificParams(jsPsych) {
  const params = readProlificParams(jsPsych);
  const realPid = Boolean(params.PROLIFIC_PID);
  if (!params.PROLIFIC_PID) {
    params.PROLIFIC_PID = `LOCAL_${jsPsych.randomization.randomID(8)}`;
  }
  params.session_start_ms = Date.now();
  jsPsych.data.addProperties(params);
  _capturedParams = params;
  // Loud, unambiguous log so it's obvious in the dev console whether
  // the real Prolific ID was captured or whether we silently fell back
  // to a LOCAL_ random id. Researchers chasing missing bonus payments
  // need this evidence to be visible.
  // eslint-disable-next-line no-console
  console.info(
    `[prolific.js] PROLIFIC_PID = ${params.PROLIFIC_PID} ` +
    `(${realPid ? 'real, from URL' : 'LOCAL fallback — no URL param found'})` +
    `; STUDY_ID = ${params.STUDY_ID ?? '(none)'}` +
    `; SESSION_ID = ${params.SESSION_ID ?? '(none)'}`,
  );
  return params;
}

/** End the session.
 *
 *  Under JATOS: submit the cumulative CSV via `jatos.endStudy` and let
 *  JATOS do the Prolific redirect itself. The Prolific completion code
 *  lives in JATOS's study settings — NOT in this bundle. Two reasons:
 *    1) The code never ships to the participant, so it can't be
 *       extracted from the HTML/JS bundle to claim completion without
 *       doing the experiment.
 *    2) Codes can be rotated in the JATOS dashboard without a redeploy.
 *  Non-`finished` reasons are passed as `errorMsg` so the JATOS
 *  dashboard surfaces the exit reason.
 *
 *  Locally (no JATOS): show a debug-friendly page summarising the
 *  captured data. The participant-facing path doesn't exist outside
 *  JATOS for the production bundle — local dev is the only non-JATOS
 *  consumer of this code path.
 *
 *  @param jsPsych   - the jsPsych instance
 *  @param reasonKey - one of the keys in END_REASONS (e.g. 'finished',
 *                     'qualificationFailed', 'browserRejected').
 */
export function endSession(jsPsych, reasonKey) {
  if (!END_REASONS.has(reasonKey)) {
    jsPsych.abortExperiment(
      `<pre>endSession: unknown end-reason key: ${reasonKey}</pre>`,
    );
    return;
  }

  if (isUnderJatos()) {
    const csv = getCleanCsv(jsPsych);
    const successful = reasonKey === 'finished';
    window.jatos.endStudy(csv, successful, successful ? null : reasonKey);
    // abortExperiment swaps the page body so any queued trials don't
    // render while JATOS is doing its end-of-study handoff.
    jsPsych.abortExperiment(renderJatosHandoffPage());
    return;
  }

  // Local dev: no JATOS, no Prolific. Just show the debug summary.
  jsPsych.abortExperiment(renderLocalEndPage(jsPsych, reasonKey));
}

// --- internal: rendering helpers ----------------------------------------

function pageWrap(inner) {
  return `
    <div style="padding:40px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                max-width:640px;margin:0 auto;color:#222;">${inner}</div>`;
}

function renderJatosHandoffPage() {
  return pageWrap(`
    <h2>Thank you — saving your data...</h2>
    <p>You will be returned to Prolific in a moment.</p>
  `);
}

function renderLocalEndPage(jsPsych, reasonKey) {
  const captured = _capturedParams ?? {};
  const nRows = jsPsych.data.get().count();
  return pageWrap(`
    <h2>Session finished (local dev mode).</h2>
    <p>Outcome: <code>${reasonKey}</code>. No JATOS detected, so no
       Prolific redirect was attempted — this page is the dev-only
       stand-in for that handoff.</p>
    <p>Data rows recorded: <code>${nRows}</code>.</p>
    <p>
      <button onclick="window.aotExport()"
              style="font-size:16px;padding:10px 20px;cursor:pointer;
                     background:#2a8c2a;color:white;border:none;border-radius:4px;">
        Download saved data
      </button>
    </p>
    <p style="color:#666;font-size:13px;">
      Downloads a JSON bundle of every per-block + final save from this
      machine's localStorage. Drop it into <code>analysis/data/</code> and open
      <code>analysis/explore.ipynb</code> to inspect.
    </p>
    <details style="margin-top:24px;">
      <summary style="cursor:pointer;color:#666;">Captured Prolific params</summary>
      <pre style="background:#f0f0f0;padding:12px;border-radius:4px;overflow:auto;">${
        JSON.stringify(captured, null, 2)
      }</pre>
    </details>
  `);
}

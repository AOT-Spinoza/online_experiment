// Prolific integration helpers.
//
// Two responsibilities:
//
//   1. Capture PROLIFIC_PID, STUDY_ID, SESSION_ID from the landing URL and
//      attach them to jsPsych's global data so every saved row carries them.
//      In the Prolific dashboard the study URL is configured as e.g.:
//        https://example.com/aot/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
//      Prolific replaces those placeholders before opening the page.
//
//   2. End the session: replace the page body with an appropriate end
//      message AND halt the jsPsych timeline so no further trials run.
//      Under Prolific this also redirects to the completion URL after a
//      short delay; locally it shows a debug-friendly page instead.

import { COMPLETION_CODES } from './config.js';
import { isUnderJatos } from './jatos_helper.js';

const PROLIFIC_PARAMS = ['PROLIFIC_PID', 'STUDY_ID', 'SESSION_ID'];

/** True if the experiment was launched from a Prolific study link
 *  (i.e. PROLIFIC_PID is present in the URL). */
export function isUnderProlific(jsPsych) {
  return Boolean(jsPsych.data.getURLVariable('PROLIFIC_PID'));
}

/** Read URL search params relevant to Prolific. Missing keys are absent
 *  in the returned object, not `null`. */
export function readProlificParams(jsPsych) {
  const out = {};
  for (const key of PROLIFIC_PARAMS) {
    const v = jsPsych.data.getURLVariable(key);
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
  if (!params.PROLIFIC_PID) {
    params.PROLIFIC_PID = `LOCAL_${jsPsych.randomization.randomID(8)}`;
  }
  params.session_start_ms = Date.now();
  jsPsych.data.addProperties(params);
  _capturedParams = params;
  return params;
}

function buildCompletionUrl(code) {
  return `https://app.prolific.com/submissions/complete?cc=${encodeURIComponent(code)}`;
}

/** End the session.
 *
 *  Under Prolific: shows a "saving..." page and redirects to the completion
 *  URL after `delayMs`.
 *  Locally: shows a debug page summarising the captured data.
 *  In both cases: halts the jsPsych timeline via `abortExperiment` so any
 *  trials that were still queued are skipped.
 *
 *  @param jsPsych - the jsPsych instance
 *  @param codeKey - one of the keys in COMPLETION_CODES (e.g. 'finished',
 *                   'qualificationFailed'). A null code shows a dead-end
 *                   thank-you page (no Prolific submission); use this for
 *                   browser-check rejections so the participant can return
 *                   the study without affecting their approval rate.
 *  @param opts.delayMs - how long to show the "saving..." page before the
 *                        actual redirect (default 1500 ms).
 */
export function endSession(jsPsych, codeKey, { delayMs = 1500 } = {}) {
  if (!(codeKey in COMPLETION_CODES)) {
    jsPsych.abortExperiment(
      `<pre>endSession: unknown completion code key: ${codeKey}</pre>`,
    );
    return;
  }
  const code = COMPLETION_CODES[codeKey];

  // Under JATOS: hand control over to JATOS, which submits the result
  // data and handles the Prolific redirect *itself*. The Prolific
  // completion code lives in JATOS's study configuration, NOT in this
  // bundle — so we deliberately do NOT pass a code or redirect URL
  // here. Two reasons:
  //   1) The code never ships to the participant, so it can't be
  //      extracted from the HTML/JS bundle to claim completion
  //      without doing the experiment.
  //   2) Codes can be rotated in the JATOS dashboard without a
  //      redeploy of the experiment bundle.
  // The codeKey is passed through as an errorMsg on non-finished
  // paths so the JATOS dashboard surfaces the exit reason.
  if (isUnderJatos()) {
    const csv = jsPsych.data.get().csv();
    const successful = codeKey === 'finished';
    window.jatos.endStudy(csv, successful, successful ? null : codeKey);
    jsPsych.abortExperiment(renderRedirectingPage('JATOS_END'));
    return;
  }

  let html;
  if (!isUnderProlific(jsPsych)) {
    html = renderLocalEndPage(jsPsych, codeKey, code);
  } else if (!code) {
    html = renderNoReturnPage();
  } else {
    html = renderRedirectingPage(code);
    setTimeout(() => window.location.replace(buildCompletionUrl(code)), delayMs);
  }
  // abortExperiment halts the timeline AND replaces the page body with the
  // HTML we pass in, so any trials that were queued behind us never render.
  jsPsych.abortExperiment(html);
}

// --- internal: rendering helpers ----------------------------------------

function pageWrap(inner) {
  return `
    <div style="padding:40px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                max-width:640px;margin:0 auto;color:#222;">${inner}</div>`;
}

function renderRedirectingPage(code) {
  const url = buildCompletionUrl(code);
  return pageWrap(`
    <h2>Thank you — saving your data...</h2>
    <p>You will be redirected to Prolific in a moment.
       If nothing happens, click <a href="${url}">here</a>.</p>
  `);
}

function renderNoReturnPage() {
  return pageWrap(`
    <h2>Thank you for your interest.</h2>
    <p>Unfortunately your browser or device does not meet the technical
    requirements for this study. Please return the study on Prolific to
    avoid affecting your approval rate.</p>
  `);
}

function renderLocalEndPage(jsPsych, codeKey, code) {
  const captured = _capturedParams ?? {};
  const nRows = jsPsych.data.get().count();
  return pageWrap(`
    <h2>Session finished (local dev mode).</h2>
    <p>Outcome: <code>${codeKey}</code> &middot; would redirect with code
       <code>${code ?? '(none — no return URL)'}</code>.</p>
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

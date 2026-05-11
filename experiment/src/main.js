// Entry point. Bootstraps jsPsych and assembles the full timeline:
//
//   capture Prolific URL vars → welcome → Layer A intro → Layer A
//   → (fail-gate)
//   → task intro → Layer B (practice + feedback + 1 catch demo)
//   → Layer C (qualification gate) → (fail-gate)
//   → main blocks loop (4 × 100 trials, mandatory rest mid-way,
//                       per-block save + a single Continue prompt)
//   → debrief survey → final save → endSession (Prolific redirect / local page)
//
// Each phase lives in its own module under src/trials/. This file is kept
// thin so the high-level flow is readable.
//
// Local-dev fallbacks: phases that need stimuli (B/C/main) will skip
// themselves with a console warning if the manifest doesn't have entries,
// so `npm run dev` runs end-to-end even before the pipeline is fully set up.

import { initJsPsych } from 'jspsych';
import 'jspsych/css/jspsych.css';

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';
import CallFunction from '@jspsych/plugin-call-function';

import { KEYS, STRUCTURE, TEST_MODE } from './config.js';
import { recordProlificParams, endSession } from './prolific.js';
import { loadStimuli } from './stimuli.js';
import { makeSaveTrial } from './data.js';
import { isUnderJatos, whenJatosReady } from './jatos_helper.js';

import { makeTrialFactories } from './trials/trial_factory.js';
import { makeConsentTrial } from './trials/consent.js';
import { layerAIntro, taskIntro } from './trials/instructions.js';
import { makeFamiliarizationTimeline } from './trials/familiarization.js';
import { makePracticeTimeline } from './trials/practice.js';
import { makeQualificationTimeline } from './trials/qualification.js';
import { makeMainBlocksTimeline } from './trials/main_blocks.js';
import { debriefSurvey } from './trials/debrief.js';

const jsPsych = initJsPsych({
  // No `on_finish` redirect here — endSession() is called explicitly from
  // a CallFunction trial at the end of the timeline so it sequences after
  // the final data-save trial. See CLAUDE.md §3.8.
});

// Everything below runs after JATOS finishes initialising (when running
// under JATOS) or immediately (when running on localhost / GitHub Pages).
// whenJatosReady is a no-op outside JATOS, so the non-JATOS path doesn't
// pay any cost for the wrap.
whenJatosReady(async () => {
  // eslint-disable-next-line no-console
  console.info(`[main.js] under JATOS: ${isUnderJatos()}`);

// 1. Capture Prolific IDs (or generate a LOCAL_* fallback for dev) before
//    any trial runs, so every saved row carries them.
const prolificParams = recordProlificParams(jsPsych);
const pid = prolificParams.PROLIFIC_PID;

// 2. Load the stimulus manifest. Awaited up-front so all phase builders
//    can synchronously consume it. If the fetch fails (network / CORS /
//    deployment misconfiguration) we render a participant-visible error
//    and halt — better than silently skipping every real-clip phase.
let stimuli;
try {
  stimuli = await loadStimuli();
} catch (e) {
  // eslint-disable-next-line no-console
  console.error('[main.js] stimulus manifest could not be loaded:', e);
  document.body.innerHTML = `
    <div style="padding:40px;font-family:sans-serif;max-width:640px;margin:0 auto;color:#222;">
      <h2>We're sorry — the experiment can't start.</h2>
      <p>The list of video clips couldn't be loaded. This usually means a
         temporary network problem; please refresh in a minute.</p>
      <p>If you keep seeing this message, please return the study on Prolific
         (so it doesn't affect your approval rate) and let the researchers
         know — your time is appreciated.</p>
      <details style="margin-top:24px;">
        <summary style="cursor:pointer;color:#666;">Technical detail (for the researchers)</summary>
        <pre style="background:#f0f0f0;padding:12px;border-radius:4px;
                    overflow:auto;font-size:12px;">${String(e).replace(/</g, '&lt;')}</pre>
      </details>
    </div>`;
  // Halt: don't run anything else.
  throw e;
}

// 3. Per-trial factories close over jsPsych.
const factories = makeTrialFactories(jsPsych);

// 4. Shared mutable state used to route between phases. Phase modules set
//    fail flags here; subsequent timeline nodes' `conditional_function`
//    inspect them.
const state = {
  consentDeclined: false,
  familiarizationFailed: false,
  qualificationFailed: false,
  // performance.now() at session start; populated by the welcome trial's
  // on_load. Used by block-end summaries and the session-cap check
  // (the only programmatic early-exit during main blocks now that the
  // Finish button has been removed in favour of forward-only flow).
  _sessionStart: null,
};

// ----- timeline pieces -------------------------------------------------

const testBanner = TEST_MODE
  ? `<div style="background:#ffe9a8;border:1px solid #c79b00;color:#5a4400;
                padding:10px 14px;border-radius:6px;margin:0 0 18px;font-size:14px;">
       <strong>TEST MODE</strong> — shortened run
       (${STRUCTURE.mainBlocks}×${STRUCTURE.trialsPerMainBlock} main trials,
       ${STRUCTURE.practiceTrials} practice, ${STRUCTURE.qualificationTrials} qualification).
       Remove <code>?test=1</code> from the URL for the full-length production run.
     </div>`
  : '';

const welcome = {
  type: HtmlKeyboardResponse,
  stimulus: `
    ${testBanner}
    <h1>Arrow of Time experiment</h1>
    <p>You'll be watching short video clips and judging whether each one is
    playing forward or backward.</p>
    <p>The session will take roughly <strong>${TEST_MODE ? '10' : '45'} minutes</strong>.
    We'll walk through the task before any real trials. Once the main blocks
    begin we ask you to complete all ${STRUCTURE.mainBlocks} of them in one
    sitting — there's a forced break partway through.</p>
    <p>Captured PID: <code>${pid}</code></p>
    <p>Press <span class="key-cap">SPACE</span> to begin.</p>
  `,
  choices: [KEYS.start],
  on_load() {
    state._sessionStart = performance.now();
  },
  data: { trial_type_tag: 'welcome', test_mode: TEST_MODE },
};

// --- Informed consent and its decline-gate ---
const consent = makeConsentTrial(state);
const consentDeclineGate = {
  timeline: [
    {
      type: CallFunction,
      func: () => endSession(jsPsych, 'consentDeclined'),
      data: { trial_type_tag: 'end_session_call', reason: 'consentDeclined' },
    },
  ],
  conditional_function: () => state.consentDeclined,
};

// --- Layer A and its fail-gate ---
const layerA = makeFamiliarizationTimeline(jsPsych, factories, state);
const layerAFailGate = {
  timeline: [
    {
      type: CallFunction,
      func: () => endSession(jsPsych, 'familiarizationFailed'),
      data: { trial_type_tag: 'end_session_call', reason: 'familiarizationFailed' },
    },
  ],
  conditional_function: () => state.familiarizationFailed,
};

// --- Layer B (practice) ---
const layerB = makePracticeTimeline(jsPsych, factories, stimuli);

// --- Layer C (qualification) and its fail-gate ---
const layerC = makeQualificationTimeline(jsPsych, factories, stimuli, state);
const layerCFailGate = {
  timeline: [
    {
      type: CallFunction,
      func: () => endSession(jsPsych, 'qualificationFailed'),
      data: { trial_type_tag: 'end_session_call', reason: 'qualificationFailed' },
    },
  ],
  conditional_function: () => state.qualificationFailed,
};

// --- Main blocks (4 × 100 trials, with mid-session rest + per-block save) ---
const mainBlocks = makeMainBlocksTimeline(jsPsych, factories, stimuli, state, pid);

// --- End: debrief, final save, redirect ---
const finalSave = makeSaveTrial(jsPsych, pid, 'final');
const finish = {
  type: CallFunction,
  func: () => endSession(jsPsych, 'finished'),
  data: { trial_type_tag: 'end_session_call', reason: 'finished' },
};

// ----- assemble + run ---------------------------------------------------

const timeline = [
  consent,
  consentDeclineGate,
  welcome,
  layerAIntro(),
  layerA,
  layerAFailGate,
  taskIntro(),
];
if (layerB) timeline.push(layerB);
if (layerC) {
  timeline.push(layerC);
  timeline.push(layerCFailGate);
}
timeline.push(...mainBlocks);
timeline.push(debriefSurvey());
timeline.push(finalSave);
timeline.push(finish);

await jsPsych.run(timeline);
}); // close whenJatosReady

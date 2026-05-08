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

import { KEYS } from './config.js';
import { recordProlificParams, endSession } from './prolific.js';
import { loadStimuli } from './stimuli.js';
import { makeSaveTrial } from './data.js';

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

// 1. Capture Prolific IDs (or generate a LOCAL_* fallback for dev) before
//    any trial runs, so every saved row carries them.
const prolificParams = recordProlificParams(jsPsych);
const pid = prolificParams.PROLIFIC_PID;

// 2. Load the stimulus manifest. Awaited up-front so all phase builders
//    can synchronously consume it.
const stimuli = await loadStimuli();

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

const welcome = {
  type: HtmlKeyboardResponse,
  stimulus: `
    <h1>Arrow of Time experiment</h1>
    <p>You'll be watching short video clips and judging whether each one is
    playing forward or backward.</p>
    <p>The session will take roughly <strong>45 minutes</strong>. We'll walk
    through the task before any real trials, and you can stop after any of
    the main blocks.</p>
    <p>Captured PID: <code>${pid}</code></p>
    <p>Press <span class="key-cap">SPACE</span> to begin.</p>
  `,
  choices: [KEYS.start],
  on_load() {
    state._sessionStart = performance.now();
  },
  data: { trial_type_tag: 'welcome' },
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

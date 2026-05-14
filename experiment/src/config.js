// Centralised constants for the experiment. Keeping every "magic number" in
// one place so any future change to timing, key mappings, or block structure
// is visible at a glance — and so the runtime values match CLAUDE.md.

// -- Response keys --------------------------------------------------------
// Direction: spatial mapping, ← points back, → points forward. No mnemonic
// to teach. Confidence: top-row number keys 1-5.
export const KEYS = {
  start: ' ',                   // spacebar starts each trial
  forward: 'ArrowRight',
  backward: 'ArrowLeft',
  confidence: ['1', '2', '3', '4', '5'],
};

// Anchor labels for the confidence scale (CLAUDE.md §3.3, §6).
export const CONFIDENCE_LABELS = [
  'LOW confidence',
  '0.25',
  '0.5',
  '0.75',
  'HIGH confidence',
];

// -- Per-trial timing (ms) ------------------------------------------------
// Source clips are uniformly 2.5 s / 60 frames @ 24 fps (CLAUDE.md §2.3).
// `direction_rt` is measured from the moment direction keys become enabled
// (i.e. the video's `ended` event). `confidence_rt` is from the confidence
// prompt's onset. The confidence prompt is participant-paced — no timeout.
export const TIMING = {
  videoMs: 2500,
  directionResponseWindowMs: 3000,
  // Familiarization HTML stimuli are shown for the same duration as a real
  // video so the response window opens at the same offset participants
  // experience throughout.
  familiarizationStimulusMs: 2500,
  itiMs: 500,
  // Brief feedback shown after each practice trial (Layer B only).
  feedbackMs: 800,
  // Mandatory rest screen between blocks 2 and 3 (CLAUDE.md §3.3).
  mandatoryRestMs: 30 * 1000,
};

// -- Test mode ------------------------------------------------------------
// Append `?test=1` to the study URL to switch to a heavily-shortened
// version that runs end-to-end in ~10 min: 4 main blocks of 20 trials
// each (19 real + 1 catch), short practice/qualification. Use this to
// dry-run the whole experiment without burning 45 min per iteration.
//
// Prolific URL params (PROLIFIC_PID, STUDY_ID, SESSION_ID) coexist with
// `?test=1` — just chain them: `...&test=1`. The flag is logged loudly
// in the console so it's hard to deploy with test mode on by accident,
// and the welcome screen shows a visible TEST-MODE banner.
function detectTestMode() {
  if (typeof window === 'undefined') return false;
  const v = new URLSearchParams(window.location.search).get('test');
  if (v == null) return false;
  return v !== '0' && v !== 'false';
}
export const TEST_MODE = detectTestMode();

// -- Block structure ------------------------------------------------------
// CLAUDE.md §3.3: 4 main blocks of 100 trials (95 real + 5 catch),
// mandatory rest between blocks 2 and 3, soft 90-min cap as a runaway-
// session safety net (the typical session is ~45 min; the cap exists so
// a participant who falls asleep on the confidence prompt doesn't spin
// indefinitely).
//
// The cap is intentionally generous — it should ONLY fire for a stalled
// session, never for a slow-but-attentive participant. An earlier 60-min
// cap was tight enough that a slow-paced pilot participant tripped it
// and lost block 4 silently. 90 min covers the slowest plausible normal
// session and still catches a sleeping participant within an hour of
// their last response.
const FULL_STRUCTURE = {
  mainBlocks: 4,
  trialsPerMainBlock: 100,
  realTrialsPerMainBlock: 95,
  catchTrialsPerMainBlock: 5,
  // Hard caps; the loop terminates when EITHER is hit.
  maxTotalMainTrials: 400,
  maxSessionMs: 90 * 60 * 1000,
  // Pre-experiment screening (CLAUDE.md §3.4).
  practiceTrials: 12,
  practiceCatchTrials: 2,            // 2 catch trials in Layer B so the format is well-rehearsed
  qualificationTrials: 10,
  qualificationCatchTrials: 1,       // 1 catch in Layer C so the format also lives in the gated phase
  qualificationPassFraction: 0.75,
  familiarizationTrials: 8,          // mixed: 2 direction, 2 confidence, 4 combined
  familiarizationMaxConsecutiveErrors: 2,
};

// Test-mode overrides. We don't touch maxSessionMs (90 min is plenty) or
// the familiarization payload (Layer A is text-only and fast already).
// `mainBlocks` stays 4 so the mid-experiment rest screen still appears
// in the same place — that flow itself is worth dry-running.
const TEST_OVERRIDES = {
  trialsPerMainBlock: 20,
  realTrialsPerMainBlock: 19,
  catchTrialsPerMainBlock: 1,
  maxTotalMainTrials: 80,
  practiceTrials: 4,
  practiceCatchTrials: 1,
  qualificationTrials: 4,
};

export const STRUCTURE = TEST_MODE
  ? { ...FULL_STRUCTURE, ...TEST_OVERRIDES }
  : FULL_STRUCTURE;

if (TEST_MODE && typeof window !== 'undefined') {
  // eslint-disable-next-line no-console
  console.warn(
    '[config.js] TEST MODE ACTIVE (URL has ?test=1). ' +
    `${STRUCTURE.mainBlocks}×${STRUCTURE.trialsPerMainBlock} main, ` +
    `${STRUCTURE.practiceTrials} practice, ${STRUCTURE.qualificationTrials} qualification. ` +
    'Remove `?test=1` for the full-length production run.',
  );
}

// -- Catch-trial bonus ----------------------------------------------------
// CLAUDE.md §3.9: payment bonus contingent on offline catch-trial pass rate.
// Communicated to participants in the consent + instructions screens.
export const CATCH_TRIAL_BONUS = {
  passFraction: 0.80,
  // Used only for display in the instructions; actual bonus is paid from
  // the Prolific dashboard after offline scoring.
  amountText: '£1.00',
};

// -- DataPipe / OSF -------------------------------------------------------
// PLACEHOLDER — replace with the real ID from the DataPipe dashboard before
// any participant-facing deployment. While this is the placeholder string,
// data.js falls back to localStorage so the experiment still runs in dev.
export const DATAPIPE_EXPERIMENT_ID = 'XXXXXXXXXXXX';

// -- End-of-session reason keys ------------------------------------------
//
// Recognized values for the `reasonKey` argument to `endSession()`. The
// bundle is JATOS-only for production: when running under JATOS, the
// reason key is forwarded to `jatos.endStudy(csv, successful, errorMsg)`
// — JATOS then handles the Prolific redirect using the completion code
// configured in JATOS's study settings. The code never ships to the
// client, so a participant can't extract it from the JS bundle.
//
// Locally (no JATOS), `endSession` just shows a debug page summarising
// the captured data — there's no Prolific redirect path in this bundle
// anymore.
//
// `finished` is the only "successful" reason; everything else is
// reported as an errorMsg so JATOS surfaces it in the dashboard.
export const END_REASONS = new Set([
  'finished',
  'familiarizationFailed',
  'qualificationFailed',
  'consentDeclined',
  'browserRejected',
  // Preload couldn't complete within the safety timeout, OR completed
  // with one or more files failed. Previous-block data is still on the
  // JATOS server (the per-block saveTrial ran before this block's
  // preload started). See preload_config.preloadHealthCheck.
  'preload_failed',
]);

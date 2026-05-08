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
  directionResponseWindowMs: 2000,
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

// -- Block structure ------------------------------------------------------
// CLAUDE.md §3.3: 4 main blocks of 100 trials (95 real + 5 catch),
// mandatory rest between blocks 2 and 3, soft 1-hour cap as a runaway-
// session safety net (the typical session is ~45 min; the cap exists so
// a participant who falls asleep on the confidence prompt doesn't spin
// indefinitely).
export const STRUCTURE = {
  mainBlocks: 4,
  trialsPerMainBlock: 100,
  realTrialsPerMainBlock: 95,
  catchTrialsPerMainBlock: 5,
  // Hard caps; the loop terminates when EITHER is hit.
  maxTotalMainTrials: 400,
  maxSessionMs: 60 * 60 * 1000,
  // Pre-experiment screening (CLAUDE.md §3.4).
  practiceTrials: 12,
  practiceCatchTrials: 1,            // 1 catch trial in Layer B for exposure
  qualificationTrials: 10,
  qualificationPassFraction: 0.75,
  familiarizationTrials: 8,          // mixed: 2 direction, 2 confidence, 4 combined
  familiarizationMaxConsecutiveErrors: 2,
};

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

// -- Prolific completion codes -------------------------------------------
// Filled in from the Prolific study dashboard before deployment. Keeping
// these as obvious placeholders so a forgotten edit fails loudly: Prolific
// will reject 'PLACEHOLDER_*' codes and we'll see it instantly.
export const COMPLETION_CODES = {
  finished:              'PLACEHOLDER_FINISHED',
  familiarizationFailed: 'PLACEHOLDER_FAMFAIL',
  qualificationFailed:   'PLACEHOLDER_QUALFAIL',
  consentDeclined:       'PLACEHOLDER_NOCONSENT',
  browserRejected:       null,
};

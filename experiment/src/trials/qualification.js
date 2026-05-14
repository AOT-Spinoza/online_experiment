// Layer C — Qualification gate (CLAUDE.md §3.4).
//
// ~10 real obvious clips, **disjoint from practice**, no per-trial feedback.
// Direction accuracy ≥ 75% to proceed; failure routes to a graceful
// end-of-session screen that pays for time spent. No catch trials in this
// layer — Layer C's signal is "can the participant do the real task on
// obvious clips?" and we keep that signal clean.
//
// Confidence is recorded for analysis but not gated.

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';
import CallFunction from '@jspsych/plugin-call-function';

import { KEYS, STRUCTURE } from '../config.js';
import { phaseHasStimuli, buildQualificationList } from '../stimuli.js';
import { preloadWithWarmup } from '../preload_config.js';

// Tag used on the catch row in Layer C so the gate calculation can
// exclude it from the direction-accuracy fraction. The catch's
// "correctness" is scored offline against the private manifest, not
// on the client, so including it in the live gate would just dilute
// the signal (we'd treat it as wrong by default).
const QUAL_CATCH_FLAG = 'is_qualification_catch';

const LAYER_NAME = 'layer_c';

function qualificationIntro() {
  return {
    type: HtmlKeyboardResponse,
    stimulus: `
      <h2>Qualification</h2>
      <p>Now ${STRUCTURE.qualificationTrials} more trials, this time without
      feedback. These clips are also unambiguous. After this short block we'll
      move on to the main task.</p>
      <p>Press <span class="key-cap">SPACE</span> to begin.</p>
    `,
    choices: [KEYS.start],
    data: { trial_type_tag: 'instructions', phase: 'qualification_intro' },
  };
}

/** Build the Layer C timeline node, or `null` if no qualification stimuli
 *  are available. The `state` object's `qualificationFailed` field is set
 *  by the gate-check at the end of the layer. */
export function makeQualificationTimeline(jsPsych, factories, stimuli, state) {
  if (!phaseHasStimuli(stimuli, 'qualification', 1)) {
    // eslint-disable-next-line no-console
    console.warn('[qualification.js] no qualification stimuli — Layer C skipped.');
    return null;
  }

  const qualList = buildQualificationList(jsPsych, stimuli);

  // Pull STRUCTURE.qualificationCatchTrials catches so the participant
  // also encounters the catch-trial format in the gated phase. We tag
  // them with QUAL_CATCH_FLAG so the gate's accuracy calculation can
  // exclude them (the catch's expected response is offline-scored
  // against the private manifest, not on the client).
  const nCatch = STRUCTURE.qualificationCatchTrials || 0;
  let catchSlots = [];
  if (nCatch > 0 && phaseHasStimuli(stimuli, 'catch', 1)) {
    const pool = jsPsych.randomization.shuffle(stimuli.catch.slice());
    for (let i = 0; i < nCatch; i++) {
      catchSlots.push(pool[i % pool.length]);
    }
  }

  const allUrls = [
    ...qualList.map(s => s.url),
    ...catchSlots.map(s => s.url),
  ];

  const [preload, healthCheck, warmup] = preloadWithWarmup({
    videos: allUrls,
    message: '<p>Loading qualification clips…</p>',
    phase: 'qualification',
    jsPsych,
  });

  const trials = qualList.map((s, i) => factories.videoTrial(
    { stimulus_id: s.stimulus_id, url: s.url },
    {
      phase: 'qualification',
      block_index: 0,
      trial_index_in_block: i,
      direction_true: s.direction,
    },
    { askConfidence: true, feedback: false },
  ));

  // Splice catches into middle positions; spread evenly when nCatch > 1.
  catchSlots.forEach((catchSlot, i) => {
    const catchTrial = factories.videoTrial(
      { stimulus_id: catchSlot.stimulus_id, url: catchSlot.url },
      {
        phase: 'qualification',
        block_index: 0,
        trial_index_in_block: -(i + 1),  // marked separately for analysis
        [QUAL_CATCH_FLAG]: true,
        // No direction_true: catch ground truth doesn't ship to the
        // client. The gate excludes this row via QUAL_CATCH_FLAG below.
      },
      { askConfidence: true, feedback: false },
    );
    const denom = catchSlots.length + 1;
    const insertAt = Math.max(2, Math.floor(trials.length * (i + 1) / denom));
    trials.splice(insertAt, 0, catchTrial);
  });

  // Final gate-check trial: compute direction accuracy on this layer
  // (EXCLUDING catch rows — see QUAL_CATCH_FLAG above) and set
  // state.qualificationFailed if below threshold. The fail-gate that
  // follows in main.js routes to endSession when the flag is set.
  const gate = {
    type: CallFunction,
    func: () => {
      const rows = jsPsych.data
        .get()
        .filter({ trial_type_tag: 'stimulus', phase: 'qualification' })
        .values()
        .filter(r => !r[QUAL_CATCH_FLAG]);
      const total = rows.length;
      const correct = rows.filter(r => r.correct === true).length;
      const fraction = total > 0 ? correct / total : 0;
      // eslint-disable-next-line no-console
      console.info(
        `[qualification.js] direction accuracy on obvious clips = ` +
        `${correct}/${total} = ${(fraction * 100).toFixed(1)}% ` +
        `(threshold ${(STRUCTURE.qualificationPassFraction * 100).toFixed(0)}%)`,
      );
      state.qualificationFailed = fraction < STRUCTURE.qualificationPassFraction;
    },
    data: { trial_type_tag: 'qualification_gate' },
  };

  return {
    name: LAYER_NAME,
    timeline: [qualificationIntro(), preload, healthCheck, warmup, ...trials, gate],
  };
}

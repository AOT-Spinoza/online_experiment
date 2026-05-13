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

  const [preload, healthCheck, warmup] = preloadWithWarmup({
    videos: qualList.map(s => s.url),
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

  // Final gate-check trial: compute direction accuracy on this layer and
  // set state.qualificationFailed if below threshold. The fail-gate that
  // follows in main.js routes to endSession when the flag is set.
  const gate = {
    type: CallFunction,
    func: () => {
      const rows = jsPsych.data
        .get()
        .filter({ trial_type_tag: 'stimulus', phase: 'qualification' })
        .values();
      const total = rows.length;
      const correct = rows.filter(r => r.correct === true).length;
      const fraction = total > 0 ? correct / total : 0;
      // eslint-disable-next-line no-console
      console.info(
        `[qualification.js] direction accuracy = ${correct}/${total} = ` +
        `${(fraction * 100).toFixed(1)}% (threshold ` +
        `${(STRUCTURE.qualificationPassFraction * 100).toFixed(0)}%)`,
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

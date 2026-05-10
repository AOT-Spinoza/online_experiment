// Layer A — Interface familiarization (CLAUDE.md §3.4).
//
// 8 short HTML trials in three flavours, randomly shuffled:
//   - direction-only (×2):  "Press → for FORWARD" / "Press ← for BACKWARD"
//   - confidence-only (×2): "Press 3" / "Press 5"  (introduces the number row)
//   - combined (×4):        "Press → for FORWARD, then press 4"
//                           (full per-trial response shape)
//
// No real-clip ground truth involved — the visible instruction is the
// answer key, so this layer leaks zero information about the main task
// even if the entire JS bundle is dumped (CLAUDE.md §3.9).
//
// Failure mode: if the participant's last K stimulus rows are all wrong,
// we set state.familiarizationFailed = true and abort the rest of the layer
// via `abortTimelineByName('layer_a')`. A guard trial after the layer routes
// the participant to a polite end-of-session screen that pays for time spent.
// (For combined trials, the consecutive-fail check looks at direction
// correctness only; confidence-correctness on the trailing confidence row
// is logged but doesn't gate.)

import CallFunction from '@jspsych/plugin-call-function';

import { STRUCTURE } from '../config.js';

const LAYER_NAME = 'layer_a';

const STYLE_BLOCK = `style="font-size:28px;line-height:1.6;text-align:center;"`;

const FORWARD_DIR = `<div ${STYLE_BLOCK}>Press <span class="key-cap">→</span> for <strong>FORWARD</strong></div>`;
const BACKWARD_DIR = `<div ${STYLE_BLOCK}>Press <span class="key-cap">←</span> for <strong>BACKWARD</strong></div>`;

function confidenceOnly(n) {
  return `<div ${STYLE_BLOCK}>Press <span class="key-cap">${n}</span></div>`;
}

function combined(direction, n) {
  const arrow = direction === 'forward' ? '→' : '←';
  const word = direction === 'forward' ? 'FORWARD' : 'BACKWARD';
  return `
    <div ${STYLE_BLOCK}>
      Press <span class="key-cap">${arrow}</span> for <strong>${word}</strong>,
      <br>
      then press <span class="key-cap">${n}</span>
    </div>`;
}

/** Build the 8 trial payloads, shuffled. */
function buildPayloads(jsPsych) {
  const items = [
    { html: FORWARD_DIR, expect_direction: 'forward' },
    { html: BACKWARD_DIR, expect_direction: 'backward' },
    { html: confidenceOnly(3), expect_confidence: 3 },
    { html: confidenceOnly(5), expect_confidence: 5 },
    // Combined: 4 trials, balanced direction × spread of confidences
    { html: combined('forward', 4), expect_direction: 'forward', expect_confidence: 4 },
    { html: combined('backward', 2), expect_direction: 'backward', expect_confidence: 2 },
    { html: combined('forward', 1), expect_direction: 'forward', expect_confidence: 1 },
    { html: combined('backward', 5), expect_direction: 'backward', expect_confidence: 5 },
  ];
  // Sanity: configured familiarizationTrials should match.
  if (items.length !== STRUCTURE.familiarizationTrials) {
    // eslint-disable-next-line no-console
    console.warn(
      `[familiarization.js] STRUCTURE.familiarizationTrials = ${STRUCTURE.familiarizationTrials} ` +
      `but the payload set has ${items.length} items.`,
    );
  }
  return jsPsych.randomization.shuffle(items);
}

/** Build the Layer A timeline node.
 *
 *  @param jsPsych    - the jsPsych instance
 *  @param factories  - return value of makeTrialFactories(jsPsych)
 *  @param state      - shared mutable object; will receive
 *                      `state.familiarizationFailed = true` if the
 *                      participant gets too many wrong in a row.
 *  @returns          - a single timeline-node object (named 'layer_a').
 */
export function makeFamiliarizationTimeline(jsPsych, factories, state) {
  const payloads = buildPayloads(jsPsych);

  const items = [];
  for (let i = 0; i < payloads.length; i++) {
    items.push(
      factories.htmlInstructionTrial(payloads[i], {
        phase: 'familiarization',
        block_index: 0,
        trial_index_in_block: i,
      }, { feedback: true }),
    );
    // After each trial, check the last K stimulus rows. If they're all
    // wrong (direction-wise), set the fail flag and abort the rest.
    items.push({
      type: CallFunction,
      func: () => {
        const recent = jsPsych.data
          .get()
          .filter({ trial_type_tag: 'stimulus', phase: 'familiarization' })
          .last(STRUCTURE.familiarizationMaxConsecutiveErrors)
          .values();
        const enough = recent.length >= STRUCTURE.familiarizationMaxConsecutiveErrors;
        const allWrong = enough && recent.every(r => r.correct === false);
        if (allWrong) {
          state.familiarizationFailed = true;
          jsPsych.abortTimelineByName(LAYER_NAME);
        }
      },
      data: { trial_type_tag: 'consecutive_fail_check' },
    });
  }

  return {
    name: LAYER_NAME,
    timeline: items,
  };
}

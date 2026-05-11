// Main blocks (CLAUDE.md §3.3, §3.5).
//
// 4 blocks of 100 trials each (95 real + 5 catch). Real and catch entries
// are indistinguishable in the public manifest by design — the runtime
// just plays whatever URLs are in the `main` array. Direction labels for
// real clips and instruction labels for catch trials live only in the
// private manifest, used by `analysis/score.py` to score offline.
//
// Per-block flow:
//   1. Preload this block's videos (plugin-preload).
//   2. Run the block's trials (videoTrial with askConfidence: true,
//      no feedback, no direction_true on the data row).
//   3. Per-block save trial (DataPipe + localStorage backup).
//   4. Block-end summary (single Continue button, no early-exit).
//
// Between blocks 2 and 3, a mandatory ≥30 s rest screen runs.
//
// Termination: a wall-clock check against STRUCTURE.maxSessionMs is the
// only programmatic short-circuit on the remaining blocks. The
// participant can still withdraw at any time per the consent (close the
// tab); per-block saves preserve data up to the last completed block.

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';

import { KEYS, STRUCTURE } from '../config.js';
import { phaseHasStimuli, buildMainBlocks } from '../stimuli.js';
import { makeBlockEndScreen, makeMandatoryRestScreen } from './block_end.js';
import { makeSaveTrial } from '../data.js';
import { preloadConfig } from '../preload_config.js';

function mainIntro() {
  // Rough wall-time estimate per block: 2.5 s video + ~3 s direction
  // window (worst case) + ~1.5 s confidence + 0.5 s ITI + 0.5 s start
  // prompt ≈ 8 s / trial. Round to the nearest minute.
  const minsPerBlock = Math.max(1, Math.round(STRUCTURE.trialsPerMainBlock * 8 / 60));
  return {
    type: HtmlKeyboardResponse,
    stimulus: `
      <h2>The experiment starts now</h2>
      <p>Practice is over. From this point on, <strong>your responses
      count</strong>. There are <strong>${STRUCTURE.mainBlocks} blocks</strong>
      of about ${minsPerBlock} minute${minsPerBlock === 1 ? '' : 's'} each
      (${STRUCTURE.trialsPerMainBlock} trials per block), with a short
      break between blocks 2 and 3.</p>
      <p>The flow is the same as in practice:
      <span class="key-cap">SPACE</span> to start each clip,
      <span class="key-cap">←</span>/<span class="key-cap">→</span>
      for backward/forward, then
      <span class="key-cap">1</span>–<span class="key-cap">5</span>
      for confidence — but <strong>no feedback this time</strong>.</p>
      <p>Press <span class="key-cap">SPACE</span> to begin block 1.</p>
    `,
    choices: [KEYS.start],
    data: { trial_type_tag: 'instructions', phase: 'main_intro' },
  };
}

/** Build the entire main-blocks segment of the timeline.
 *
 *  Returns an array of timeline nodes. If the main pool is empty (no
 *  manifest yet), returns an empty array and warns; the experiment
 *  continues to the debrief without main blocks.
 */
export function makeMainBlocksTimeline(jsPsych, factories, stimuli, state, pid) {
  if (!phaseHasStimuli(stimuli, 'main', 1)) {
    // eslint-disable-next-line no-console
    console.warn('[main_blocks.js] main pool is empty — main blocks skipped.');
    return [];
  }

  const blocks = buildMainBlocks(jsPsych, stimuli);
  const items = [mainIntro()];

  for (let i = 0; i < blocks.length; i++) {
    const trialList = blocks[i];
    if (trialList.length === 0) continue;

    // Mandatory rest before block index 2 (i.e. between blocks 2 and 3).
    if (i === 2) {
      items.push({
        timeline: [makeMandatoryRestScreen()],
        conditional_function: () => shouldContinueBlocks(state),
      });
    }

    const preload = preloadConfig({
      videos: trialList.map(s => s.url),
      message: `<p>Loading block ${i + 1} clips…</p>`,
      phase: 'main',
      blockIndex: i,
    });

    const trials = trialList.map((s, j) => factories.videoTrial(
      { stimulus_id: s.stimulus_id, url: s.url },
      {
        phase: 'main',
        block_index: i,
        trial_index_in_block: j,
        // No direction_true on main rows — bot-resistance constraint
        // (CLAUDE.md §3.9).
      },
      { askConfidence: true, feedback: false },
    ));

    const blockSave = makeSaveTrial(jsPsych, pid, `block${i}`);
    const blockEnd = makeBlockEndScreen(jsPsych, i, state, state._sessionStart);

    items.push({
      timeline: [preload, ...trials, blockSave, blockEnd],
      conditional_function: () => shouldContinueBlocks(state),
    });
  }

  return items;
}

function shouldContinueBlocks(state) {
  if (state._sessionStart != null) {
    const elapsed = performance.now() - state._sessionStart;
    if (elapsed >= STRUCTURE.maxSessionMs) return false;
  }
  return true;
}

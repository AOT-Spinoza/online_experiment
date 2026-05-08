// Layer B — Practice block (CLAUDE.md §3.4).
//
// ~12 real obvious clips with per-trial direction feedback. Plus 1 catch
// trial so the participant encounters the catch-trial format once with
// feedback before main blocks.
//
// Ground truth IS shipped to the client for these clips because the
// per-trial feedback requires it. The clips never appear in main blocks,
// so the leak is bounded (CLAUDE.md §2.4 / §3.9).
//
// If the practice pool is empty (no manifest yet, e.g. running the smoke
// test before the pipeline is set up), we skip Layer B entirely with a
// console warning. The experiment continues to Layer C / main blocks (which
// will likely also skip, gracefully).

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';
import PreloadPlugin from '@jspsych/plugin-preload';

import { KEYS } from '../config.js';
import { phaseHasStimuli, buildPracticeList } from '../stimuli.js';

const LAYER_NAME = 'layer_b';

function practiceIntro() {
  return {
    type: HtmlKeyboardResponse,
    stimulus: `
      <h2>Practice — these are <em>not</em> real experiment trials</h2>
      <p>You'll now see a small number of real video clips,
      <strong>with feedback after each one</strong>, so you can calibrate
      your responses. These clips are unambiguous and they
      <strong>do not count toward the experiment</strong> — they're just
      to help you get used to the task before the real trials begin.</p>
      <p>One of these trials may be an <strong>attention-check instruction</strong>
      (a video showing on-screen text rather than a clip). When you see one,
      just follow exactly what it says.</p>
      <p>Press <span class="key-cap">SPACE</span> to begin practice.</p>
    `,
    choices: [KEYS.start],
    data: { trial_type_tag: 'instructions', phase: 'practice_intro' },
  };
}

/** Build the Layer B timeline node, or `null` if no practice stimuli are
 *  available. */
export function makePracticeTimeline(jsPsych, factories, stimuli) {
  if (!phaseHasStimuli(stimuli, 'practice', 1)) {
    // eslint-disable-next-line no-console
    console.warn('[practice.js] no practice stimuli — Layer B skipped.');
    return null;
  }

  const practiceList = buildPracticeList(jsPsych, stimuli);

  // Pull one real catch trial from the public manifest's `catch` array
  // so the participant sees the catch-trial format once in practice.
  // No feedback on this slot — the catch entry's expected response
  // doesn't ship to the client (we score offline against the private
  // manifest). The participant just sees the on-screen instruction,
  // follows it, and proceeds. They've already done many real practice
  // trials with feedback by this point so they understand the response
  // shape; the demo just exposes them to the catch format itself.
  let catchSlot = null;
  if (phaseHasStimuli(stimuli, 'catch', 1)) {
    const pool = jsPsych.randomization.shuffle(stimuli.catch.slice());
    catchSlot = pool[0];
  }

  // Preload everything we'll need this layer.
  const allUrls = [
    ...practiceList.map(s => s.url),
    ...(catchSlot ? [catchSlot.url] : []),
  ];
  const preload = {
    type: PreloadPlugin,
    video: allUrls,
    show_progress_bar: true,
    auto_preload: false,
    message: '<p>Loading practice clips… this may take a few seconds.</p>',
    data: { trial_type_tag: 'preload', phase: 'practice' },
  };

  const trials = practiceList.map((s, i) => factories.videoTrial(
    { stimulus_id: s.stimulus_id, url: s.url },
    {
      phase: 'practice',
      block_index: 0,
      trial_index_in_block: i,
      direction_true: s.direction,
    },
    { askConfidence: true, feedback: true },
  ));

  // Insert the catch-trial demo at a random middle position so it's not
  // the very first or last trial.
  if (catchSlot) {
    const catchTrial = factories.videoTrial(
      { stimulus_id: catchSlot.stimulus_id, url: catchSlot.url },
      {
        phase: 'practice',
        block_index: 0,
        trial_index_in_block: -1,   // marked separately for analysis
        is_practice_catch_demo: true,
        // No direction_true: we don't have it for main entries, and
        // we don't want to score correctness on this slot.
      },
      { askConfidence: true, feedback: false },
    );
    const insertAt = 4 + Math.floor((trials.length - 4) * 0.5);
    trials.splice(insertAt, 0, catchTrial);
  }

  return {
    name: LAYER_NAME,
    timeline: [practiceIntro(), preload, ...trials],
  };
}

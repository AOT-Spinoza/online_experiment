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

import { KEYS, STRUCTURE } from '../config.js';
import { phaseHasStimuli, buildPracticeList } from '../stimuli.js';
import { preloadWithWarmup } from '../preload_config.js';

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

  // Pull STRUCTURE.practiceCatchTrials catches from the public manifest's
  // `catch` array so the participant rehearses the catch-trial format
  // before the main blocks count for real. No feedback on these slots —
  // the catch entry's expected response doesn't ship to the client (we
  // score offline against the private manifest). They've already done
  // many real practice trials with feedback by the time the catches
  // splice in, so the response shape is familiar; the demos just expose
  // them to the on-screen-instruction format itself.
  const nCatch = STRUCTURE.practiceCatchTrials || 0;
  let catchSlots = [];
  if (nCatch > 0 && phaseHasStimuli(stimuli, 'catch', 1)) {
    const pool = jsPsych.randomization.shuffle(stimuli.catch.slice());
    for (let i = 0; i < nCatch; i++) {
      catchSlots.push(pool[i % pool.length]);
    }
  }

  // Preload everything we'll need this layer.
  const allUrls = [
    ...practiceList.map(s => s.url),
    ...catchSlots.map(s => s.url),
  ];
  const [preload, healthCheck, warmup] = preloadWithWarmup({
    videos: allUrls,
    message: '<p>Loading practice clips… this may take a few seconds.</p>',
    phase: 'practice',
    jsPsych,
  });

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

  // Insert the catch demos at spread-out middle positions so they're
  // never the very first/last trials and never back-to-back. With
  // ~11 real practice trials, spacing them at thirds of the list is
  // a simple and predictable layout.
  catchSlots.forEach((catchSlot, i) => {
    const catchTrial = factories.videoTrial(
      { stimulus_id: catchSlot.stimulus_id, url: catchSlot.url },
      {
        phase: 'practice',
        block_index: 0,
        trial_index_in_block: -(i + 1),   // marked separately for analysis
        is_practice_catch_demo: true,
        // No direction_true: we don't have it for main entries, and
        // we don't want to score correctness on this slot.
      },
      { askConfidence: true, feedback: false },
    );
    const denom = catchSlots.length + 1;
    const insertAt = Math.max(2, Math.floor(trials.length * (i + 1) / denom));
    trials.splice(insertAt, 0, catchTrial);
  });

  return {
    name: LAYER_NAME,
    timeline: [practiceIntro(), preload, healthCheck, warmup, ...trials],
  };
}

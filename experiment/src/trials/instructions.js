// Instructions screens shown before Layer A and again before Layer B.
//
// Two screens:
//   - layerAIntro: short interface check briefing (just direction + number keys)
//   - taskIntro:   the full task explanation including the confidence rating,
//                  the "occasional instruction trials" disclosure (= catch
//                  trials, framed as attention checks), and the bonus.
//
// Bonus disclosure (CLAUDE.md §3.9): we tell participants UPFRONT that some
// trials will be on-screen instructions and that there's a payment bonus
// contingent on responding correctly to enough of them. This is informed
// consent for the contingent payment and removes the gameability concern
// (we never reveal mid-experiment how many they've passed).

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';

import { KEYS, CONFIDENCE_LABELS, CATCH_TRIAL_BONUS } from '../config.js';

export function layerAIntro() {
  return {
    type: HtmlKeyboardResponse,
    stimulus: `
      <h2>Quick interface check</h2>
      <p>Before the real task we'll run a few short trials to make sure you
      can read on-screen instructions and use your keyboard.</p>
      <p>You'll see a short message. Press the key it asks you to press.</p>
      <p style="margin-top:24px;">
        <span class="key-cap">→</span> = <strong>FORWARD</strong>
        &nbsp;·&nbsp;
        <span class="key-cap">←</span> = <strong>BACKWARD</strong>
        &nbsp;·&nbsp;
        <span class="key-cap">1</span>–<span class="key-cap">5</span> = confidence
      </p>
      <p>Press <span class="key-cap">SPACE</span> to begin.</p>
    `,
    choices: [KEYS.start],
    data: { trial_type_tag: 'instructions', phase: 'layerA_intro' },
  };
}

export function taskIntro() {
  const conf = CONFIDENCE_LABELS
    .map((l, i) => `<span class="key-cap">${i + 1}</span> ${l}`)
    .join(' &nbsp;·&nbsp; ');

  return {
    type: HtmlKeyboardResponse,
    stimulus: `
      <h2>The task</h2>
      <p>You'll see short video clips. Each clip has been played either
      <strong>forward</strong> (in normal time) or <strong>backward</strong>
      (in reverse). Your job is to tell which.</p>

      <p>Each trial works like this:</p>
      <ol style="font-size:18px;line-height:1.7;">
        <li>You press <span class="key-cap">SPACE</span> when ready.</li>
        <li>The video plays for 2.5 seconds.</li>
        <li>You press
          <span class="key-cap">→</span> for FORWARD or
          <span class="key-cap">←</span> for BACKWARD.
          You have 1 second.</li>
        <li>You rate how confident you are using
          <span class="key-cap">1</span>–<span class="key-cap">5</span>
          (no time limit on this).</li>
      </ol>

      <p style="margin-top:14px;">Confidence scale:
        <br><span style="font-size:15px;">${conf}</span></p>

      <h3 style="margin-top:32px;">Attention checks &amp; bonus</h3>
      <p>A small fraction of trials will not be real videos but on-screen
      <strong>instructions</strong> (e.g. "Press FORWARD, then press 3").
      This is a simple but important attention check &mdash; please
      <strong>follow the instructions exactly</strong>. There are only a few
      of these checks. If you respond correctly to at least
      <strong>${Math.round(CATCH_TRIAL_BONUS.passFraction * 100)}%</strong>
      of them, you will receive an additional bonus of
      <strong>${CATCH_TRIAL_BONUS.amountText}</strong>;
      <strong>otherwise we may have to exclude your data</strong>.</p>
      <p>You will see one of these checks during the practice and the
      qualification rounds below, so you know what to expect.</p>

      <p style="margin-top:28px;">Press <span class="key-cap">SPACE</span> to continue.</p>
    `,
    choices: [KEYS.start],
    data: { trial_type_tag: 'instructions', phase: 'task_intro' },
  };
}

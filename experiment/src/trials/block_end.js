// Block-end summary screen (CLAUDE.md §3.3, §3.7).
//
// Shown after each main block. Reports trials completed, median direction
// RT, elapsed time, accrued bonus. **No accuracy is shown for main blocks**
// (no client-side ground truth — see §3.7).
//
// In the production-online build there is **only a Continue button** —
// the participant has to commit to all 4 blocks once they've passed
// qualification. They can still withdraw at any time per the consent
// (close the tab); the per-block save in the preceding trial means a
// withdrawal mid-experiment still preserves data up to the last block.
// The 1-hour session cap (§3.3, runaway-session safety net) remains the
// only programmatic early-exit.
//
// The mandatory rest screen (between blocks 2 and 3) is a separate
// trial that runs unconditionally before the third block — it's NOT a
// post-block screen. See main_blocks.js.

import HtmlButtonResponse from '@jspsych/plugin-html-button-response';

import { STRUCTURE } from '../config.js';

function median(xs) {
  if (!xs.length) return null;
  const s = xs.slice().sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
}

function formatMs(ms) {
  if (!ms || ms < 0) return '—';
  const totalSec = Math.round(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** Build a per-block summary trial. Single Continue button — the
 *  production flow no longer offers a between-block early-exit.
 *
 *  @param jsPsych      - the jsPsych instance
 *  @param blockIndex   - 0-indexed block we just finished
 *  @param state        - shared mutable object (currently unused here;
 *                        kept on the signature for symmetry with the
 *                        other phase builders).
 *  @param sessionStart - performance.now() value captured at session
 *                        start, used to compute elapsed time.
 */
export function makeBlockEndScreen(jsPsych, blockIndex, state, sessionStart) {
  const isLast = blockIndex >= STRUCTURE.mainBlocks - 1;

  return {
    type: HtmlButtonResponse,
    choices: isLast ? ['Continue to survey'] : ['Continue'],
    data: {
      trial_type_tag: 'block_end',
      block_index: blockIndex,
    },
    stimulus: () => {
      const allMain = jsPsych.data.get()
        .filter({ trial_type_tag: 'stimulus', phase: 'main' })
        .values();
      const thisBlock = allMain.filter(r => r.block_index === blockIndex);

      const answered = thisBlock.filter(r => r.response != null);
      const missed = thisBlock.length - answered.length;

      const dirRts = answered.map(r => r.direction_rt).filter(rt => rt != null);
      const medRt = median(dirRts);

      // Lapse heuristic (CLAUDE.md §3.7): direction RT below ~250 ms is
      // implausibly fast for a real perceptual judgement (probably a key
      // already held / random tap). Count those as "fast lapses" so the
      // participant gets a soft signal that something's off.
      const fastLapses = answered.filter(r => r.direction_rt != null && r.direction_rt < 250).length;

      const elapsed = sessionStart != null
        ? performance.now() - sessionStart
        : null;

      const blockNum = blockIndex + 1;
      const totalBlocks = STRUCTURE.mainBlocks;

      const headline = isLast
        ? `Block ${blockNum}/${totalBlocks} complete — that's all the main blocks.`
        : `Block ${blockNum}/${totalBlocks} complete.`;
      const cta = isLast
        ? `<p>One short survey to go and you're done.</p>`
        : `<p>Take a quick breath, then click <strong>Continue</strong>
           to start the next block. Your data from this block is already
           saved.</p>`;

      // Soft warning lines — only render when there's something to flag,
      // so the screen stays clean for an attentive participant.
      let warnings = '';
      if (missed > 0) {
        warnings += `<li style="color:#c43b3b;">Missed responses (no key in time): <strong>${missed}</strong></li>`;
      }
      if (fastLapses > 0) {
        warnings += `<li style="color:#c43b3b;">Very fast responses (&lt;250&nbsp;ms): <strong>${fastLapses}</strong></li>`;
      }

      return `
        <h2>${headline}</h2>
        <ul style="list-style:none;padding:0;font-size:18px;line-height:1.7;">
          <li>Trials this block: <strong>${thisBlock.length}</strong>
              (${answered.length} answered, ${missed} missed)</li>
          <li>Median response time: <strong>${medRt != null ? Math.round(medRt) + ' ms' : '—'}</strong></li>
          <li>Elapsed: <strong>${formatMs(elapsed)}</strong></li>
          <li>Trials completed (main): <strong>${allMain.length} / ${STRUCTURE.maxTotalMainTrials}</strong></li>
          ${warnings}
        </ul>
        ${cta}
      `;
    },
    // No on_finish: the only button always advances. The 1-hour cap in
    // shouldContinueBlocks() is the sole programmatic path that can stop
    // the loop early.
  };
}

/** Mandatory rest screen between blocks 2 and 3 (CLAUDE.md §3.3). */
export function makeMandatoryRestScreen() {
  return {
    type: HtmlButtonResponse,
    choices: ['Continue'],
    button_html: (choice) => `<button class="jspsych-btn" id="rest-continue" disabled>${choice}</button>`,
    stimulus: `
      <h2>Halfway break</h2>
      <p>Take a 30-second break — stretch, blink, sit back. The Continue
      button will activate when the timer is up.</p>
      <p style="font-size:48px;margin:30px 0;" id="rest-countdown">30</p>
    `,
    on_load() {
      const btn = document.getElementById('rest-continue');
      const cd = document.getElementById('rest-countdown');
      let s = 30;
      const tick = () => {
        s -= 1;
        if (s > 0) {
          if (cd) cd.textContent = String(s);
        } else {
          if (cd) cd.textContent = '0';
          if (btn) btn.disabled = false;
          clearInterval(handle);
        }
      };
      const handle = setInterval(tick, 1000);
    },
    data: { trial_type_tag: 'mandatory_rest' },
  };
}

// Per-trial factory.
//
// Every trial in this experiment — real video clip, catch trial, or HTML
// instruction in Layer A — has a uniform outer shape:
//
//     [ "press SPACE to start" prompt ]   participant-paced
//     [ stimulus                     ]    2.5 s, no responses accepted
//     [ direction response window    ]    up to 1 s after stimulus ends
//     [ confidence prompt            ]    only when askConfidence: true.
//                                          participant-paced; skipped if
//                                          direction was missed.
//     [ optional feedback            ]    practice only (CLAUDE.md §3.4)
//     [ blank ITI                    ]    ~500 ms
//
// Design notes:
//   - The factory takes a jsPsych instance as a closure argument because
//     the feedback trial and the conditional confidence skip both need to
//     read the most recent stimulus row from the live data store.
//   - For VIDEO trials we use plugin-video-keyboard-response with
//     `response_allowed_while_playing: false`, so the plugin measures RT
//     from the video's `ended` event (CLAUDE.md §3.3).
//   - HTML INSTRUCTION trials (Layer A familiarization) come in three
//     flavours via `htmlInstructionTrial`: direction-only, confidence-only,
//     and combined direction-then-confidence. Same outer shape, same
//     timing as the video case.
//   - We do NOT include `direction_true` on rows where ground truth
//     shouldn't ship to the client. Callers omit it for main-task trials.
//     See CLAUDE.md §3.9.

import HtmlKeyboardResponse from '@jspsych/plugin-html-keyboard-response';
import VideoKeyboardResponse from '@jspsych/plugin-video-keyboard-response';

import { KEYS, TIMING, CONFIDENCE_LABELS } from '../config.js';

// Convert a literal direction-key name to a 'forward' / 'backward' label.
// Returns `null` if the participant didn't press one of the two response
// keys (e.g. no response within the window).
function keyToDirection(key) {
  if (key === KEYS.forward) return 'forward';
  if (key === KEYS.backward) return 'backward';
  return null;
}

// HTML shown the moment the video ends, prompting the direction response.
// Large + clean so it's an unmistakable visual cue that "respond now" — the
// silent post-video dead zone (where the participant sees a paused last
// frame with nothing telling them to act) was the source of missed trials.
const RESPONSE_PROMPT_HTML = `
  <div style="font-size:44px;line-height:1.6;text-align:center;font-weight:600;">
    <span class="key-cap" style="font-size:40px;padding:10px 24px;">←</span>&nbsp;backward
    &nbsp;&nbsp;<span style="color:#888;font-weight:400;">or</span>&nbsp;&nbsp;
    forward&nbsp;<span class="key-cap" style="font-size:40px;padding:10px 24px;">→</span>
  </div>`;

// HTML for the 1-5 confidence scale, with anchor labels under each cap.
function confidencePromptHtml() {
  const cells = CONFIDENCE_LABELS.map((label, i) => `
    <div style="display:inline-block;text-align:center;margin:0 14px;min-width:90px;">
      <span class="key-cap" style="font-size:24px;padding:8px 16px;">${i + 1}</span>
      <div style="font-size:14px;color:#555;margin-top:8px;line-height:1.3;">${label}</div>
    </div>`).join('');
  return `
    <div style="font-size:24px;line-height:1.4;margin-bottom:24px;">
      How confident are you?
    </div>
    <div style="margin: 30px 0;">${cells}</div>
    <div style="font-size:16px;color:#666;margin-top:20px;">
      Press <strong>1</strong>–<strong>5</strong> on your keyboard.
    </div>`;
}

/** Build the family of trial constructors, all closing over `jsPsych`. */
export function makeTrialFactories(jsPsych) {
  // ------------------------------------------------------------------
  // Shared sub-trials
  // ------------------------------------------------------------------

  function startPrompt() {
    return {
      type: HtmlKeyboardResponse,
      stimulus: `
        <p style="font-size:24px;">
          Press <span class="key-cap">SPACE</span> to start the next trial.
        </p>`,
      choices: [KEYS.start],
      data: { trial_type_tag: 'start_prompt' },
    };
  }

  function itiBlank() {
    return {
      type: HtmlKeyboardResponse,
      stimulus: '',
      choices: 'NO_KEYS',
      trial_duration: TIMING.itiMs,
      data: { trial_type_tag: 'iti' },
    };
  }

  // Confidence prompt — appended after a video or instruction stimulus
  // when `askConfidence` is true. Skipped (via conditional_function) when
  // the preceding stimulus had no direction response.
  function confidenceSubTrial(meta) {
    return {
      timeline: [
        {
          type: HtmlKeyboardResponse,
          stimulus: confidencePromptHtml(),
          choices: KEYS.confidence,
          response_ends_trial: true,
          // No trial_duration — participant-paced (CLAUDE.md §3.3).
          data: { trial_type_tag: 'confidence', ...meta },
          on_finish(data) {
            data.confidence = data.response != null ? parseInt(data.response, 10) : null;
            data.confidence_rt = data.rt;
          },
        },
      ],
      conditional_function: () => {
        // Skip if the most-recent stimulus row had no direction response
        // (i.e. participant timed out on the direction window).
        const last = jsPsych.data
          .get()
          .filter({ trial_type_tag: 'stimulus' })
          .last(1)
          .values()[0];
        return Boolean(last && last.response != null);
      },
    };
  }

  // Brief "Correct" / "Not quite" feedback shown after a practice or
  // familiarization trial. Reads the most recent stimulus row plus, if
  // it belongs to the same trial, the most recent confidence row — that
  // way Layer A combined trials ("Press → for FORWARD, then press 4")
  // require BOTH presses to be right for "Correct". Practice video
  // trials don't ship `expected_confidence`, so the confidence row's
  // `confidence_correct` is undefined and is ignored — the feedback
  // there falls back to direction-only correctness, as before.
  function feedbackTrial() {
    return {
      type: HtmlKeyboardResponse,
      choices: 'NO_KEYS',
      trial_duration: TIMING.feedbackMs,
      data: { trial_type_tag: 'feedback' },
      stimulus: () => {
        const lastStim = jsPsych.data
          .get()
          .filter({ trial_type_tag: 'stimulus' })
          .last(1)
          .values()[0];
        if (!lastStim) return '';
        if (lastStim.response == null) {
          return `<div style="font-size:32px;color:#888;">Too slow</div>`;
        }
        let ok = lastStim.correct === true;

        // For combined trials the confidence response is on a separate
        // row. If that row exists, belongs to this trial, and reports
        // confidence_correct === false, the trial is also "not quite".
        const lastConf = jsPsych.data
          .get()
          .filter({ trial_type_tag: 'confidence' })
          .last(1)
          .values()[0];
        if (
          lastConf
          && lastConf.block_index === lastStim.block_index
          && lastConf.trial_index_in_block === lastStim.trial_index_in_block
          && lastConf.confidence_correct === false
        ) {
          ok = false;
        }

        const color = ok ? '#2a8c2a' : '#c43b3b';
        const text = ok ? 'Correct' : 'Not quite';
        return `<div style="font-size:36px;color:${color};">${text}</div>`;
      },
    };
  }

  // ------------------------------------------------------------------
  // videoTrial — used for Layers B, C, and main blocks (and catch trials,
  // which the runtime can't distinguish from real clips by design).
  //
  //   payload: { stimulus_id, url }
  //   meta:    extra fields stored on the stimulus row (phase, block_index,
  //            trial_index_in_block, ...). For phases where ground truth
  //            ships (practice + qualification), pass
  //            { direction_true: 'forward' | 'backward' }. Omit for main.
  //   options: { feedback?: boolean, askConfidence?: boolean }
  //
  // Implemented as TWO sequential sub-trials:
  //
  //   (a) video_play  — VideoKeyboardResponse with choices='NO_KEYS' and
  //                      trial_ends_after_video=true. Plays the clip, no
  //                      responses accepted. Auto-advances on `ended`.
  //   (b) stimulus    — HtmlKeyboardResponse showing a large response
  //                      prompt (←/→). 1-s window, accepts the direction
  //                      keys. RT measured from prompt onset (= the moment
  //                      responses become possible) so the data semantics
  //                      match the spec.
  //
  // Splitting like this gives a clean visual cue ("respond now") the moment
  // the video ends — without it, participants miss trials because there's
  // no signal that the response window has opened.
  // ------------------------------------------------------------------
  function videoTrial(payload, meta = {}, options = {}) {
    const { stimulus_id, url } = payload;
    const askConfidence = options.askConfidence !== false;  // default: yes

    const videoPlay = {
      type: VideoKeyboardResponse,
      stimulus: [url],
      choices: 'NO_KEYS',
      response_allowed_while_playing: false,
      trial_ends_after_video: true,
      // Safety net in case the video's `ended` event doesn't fire (e.g.
      // a corrupt file). Generous-ish so a normal 2.5-s clip never trips
      // it before its own `ended` fires.
      trial_duration: TIMING.videoMs + 500,
      width: null,
      data: {
        trial_type_tag: 'video_play',
        stimulus_id,
        ...meta,
      },
      on_finish(data) {
        // play_completed: true iff the video actually ran (rt is set when
        // ended fires before the safety timeout).
        data.play_completed = data.rt != null && data.rt < TIMING.videoMs + 400;
      },
    };

    const responsePrompt = {
      type: HtmlKeyboardResponse,
      stimulus: RESPONSE_PROMPT_HTML,
      choices: [KEYS.forward, KEYS.backward],
      trial_duration: TIMING.directionResponseWindowMs,
      response_ends_trial: true,
      data: {
        trial_type_tag: 'stimulus',
        stimulus_id,
        ...meta,
      },
      on_finish(data) {
        data.response_direction = keyToDirection(data.response);
        data.direction_rt = data.rt;
        if (meta.direction_true) {
          data.correct = data.response_direction === meta.direction_true;
        }
      },
    };

    const items = [startPrompt(), videoPlay, responsePrompt];
    if (askConfidence) {
      items.push(confidenceSubTrial({ stimulus_id, ...meta }));
    }
    if (options.feedback && meta.direction_true) {
      items.push(feedbackTrial());
    }
    items.push(itiBlank());
    return { timeline: items };
  }

  // ------------------------------------------------------------------
  // htmlInstructionTrial — Layer A. Three flavours:
  //
  //   direction-only:    payload = { html, expect_direction }
  //   confidence-only:   payload = { html, expect_confidence }
  //   combined:          payload = { html, expect_direction, expect_confidence }
  //
  // The first two collect a single response; the combined collects both
  // (direction first, then confidence), mirroring the main-task shape.
  //
  // Unlike the video case, we do NOT insert a no-input display window
  // before the response: HTML text is read instantly, so making the
  // participant wait 2.5 s before being allowed to respond just feels
  // sluggish. The response window opens immediately on stimulus onset.
  // RT for these trials is therefore measured from text appearance.
  // ------------------------------------------------------------------
  function htmlInstructionTrial(payload, meta = {}, options = {}) {
    const { html, expect_direction, expect_confidence } = payload;
    if (!expect_direction && !expect_confidence) {
      throw new Error('htmlInstructionTrial: need expect_direction and/or expect_confidence');
    }

    const items = [startPrompt()];

    // Direction response (when expected).
    if (expect_direction) {
      items.push({
        type: HtmlKeyboardResponse,
        stimulus: html,
        choices: [KEYS.forward, KEYS.backward],
        // Layer A is more forgiving than main — give the participant time
        // to figure out the keys. Still bounded so a non-responsive
        // participant doesn't sit forever.
        trial_duration: 5 * 1000,
        response_ends_trial: true,
        data: {
          trial_type_tag: 'stimulus',
          direction_true: expect_direction,
          ...meta,
        },
        on_finish(data) {
          data.response_direction = keyToDirection(data.response);
          data.direction_rt = data.rt;
          // For direction-only trials this is the trial-level correctness;
          // for combined trials it's overwritten downstream after the
          // confidence response (see below).
          data.correct = data.response_direction === expect_direction;
        },
      });
    }

    // Confidence response (when expected).
    if (expect_confidence) {
      // For confidence-only trials this row IS the stimulus row (used by
      // the consecutive-fail check); for combined trials it's a follow-up
      // confidence row.
      const isStandalone = !expect_direction;
      items.push({
        type: HtmlKeyboardResponse,
        stimulus: html,
        choices: KEYS.confidence,
        trial_duration: 5 * 1000,
        response_ends_trial: true,
        data: {
          trial_type_tag: isStandalone ? 'stimulus' : 'confidence',
          expected_confidence: expect_confidence,
          ...meta,
        },
        on_finish(data) {
          data.confidence = data.response != null ? parseInt(data.response, 10) : null;
          data.confidence_rt = data.rt;
          if (isStandalone) {
            // Treat the confidence-only trial like a direction trial for
            // correctness purposes.
            data.correct = data.confidence === expect_confidence;
          } else {
            // Combined trial — annotate this row with confidence-correct
            // flag for analysis, but the trial-level correctness signal
            // for the consecutive-fail gate stays on the direction row.
            data.confidence_correct = data.confidence === expect_confidence;
          }
        },
      });
    }

    // Optional immediate feedback ("Correct" / "Not quite"). For Layer A
    // the participant otherwise gets no signal that they pressed the
    // wrong key — the trial just advances. The consecutive-fail check
    // catches persistent confusion, but a one-off slip should still get
    // a soft signal so the participant learns the mapping.
    if (options.feedback) {
      items.push(feedbackTrial());
    }

    items.push(itiBlank());
    return { timeline: items };
  }

  return { videoTrial, htmlInstructionTrial };
}

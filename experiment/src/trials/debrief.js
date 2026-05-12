// Final debrief survey (CLAUDE.md §3.3 step 11).
//
// We collect ONLY:
//   - Whether anything seemed unusual / off (free-text, optional)
//   - Free-text comments (optional)
//
// **No personal information.** Earlier versions asked for age and gender;
// these were removed to keep the dataset entirely free of demographic /
// personal data and simplify data management (no special-category fields,
// no separate demographics processing). The Prolific PID still ships on
// every row — that's the minimum needed for payment and per-participant
// joins — but no participant-typed identifying info is collected here.
//
// The "anything unusual" question stays because it's a cheap bot-tell
// (humans sometimes notice that some clips were obviously reversed, which
// a scraping bot wouldn't) and a stimulus-quality check. Both remaining
// fields are optional and free-text — never required to advance.

import SurveyHtmlForm from '@jspsych/plugin-survey-html-form';

export function debriefSurvey() {
  const html = `
    <div style="text-align:left;max-width:540px;">
      <h2>One last quick page</h2>
      <p>Thanks for sticking with it. Two short optional comment fields
      below — both can be left blank.</p>

      <p>
        <label for="noticed">Did anything seem unusual or off about any of
        the clips? (optional)</label><br>
        <textarea id="noticed" name="noticed" rows="3" cols="60"
                  style="font-size:14px;padding:6px;width:100%;"></textarea>
      </p>

      <p>
        <label for="comments">Any other feedback or comments? (optional)</label><br>
        <textarea id="comments" name="comments" rows="3" cols="60"
                  style="font-size:14px;padding:6px;width:100%;"></textarea>
      </p>
    </div>
  `;
  return {
    type: SurveyHtmlForm,
    html,
    button_label: 'Submit and finish',
    data: { trial_type_tag: 'debrief_survey' },
  };
}

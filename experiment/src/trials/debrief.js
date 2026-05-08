// Final debrief survey (CLAUDE.md §3.3 step 11).
//
// Brief. We collect:
//   - Age (text-input number, optional)
//   - Whether anything seemed unusual / off (free-text, optional)
//   - Free-text comments (optional)
//
// We deliberately keep this short — long surveys at the end of a long
// session lower data quality and frustrate participants. The "anything
// unusual" question is a cheap bot-tell (humans sometimes notice that some
// clips were obviously reversed, which a bot wouldn't) and a stimulus-
// quality check.

import SurveyHtmlForm from '@jspsych/plugin-survey-html-form';

export function debriefSurvey() {
  const html = `
    <div style="text-align:left;max-width:540px;">
      <h2>One last quick page</h2>
      <p>Thanks for sticking with it. The two demographic questions you agreed
      to in the consent form, plus two short optional comment fields.</p>

      <p>
        <label for="age">Age (years):</label><br>
        <input type="number" id="age" name="age" min="18" max="100"
               style="width:100px;padding:6px;font-size:16px;">
      </p>

      <p>
        <label for="gender">Gender:</label><br>
        <select id="gender" name="gender"
                style="padding:6px;font-size:16px;min-width:200px;">
          <option value=""></option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="non-binary">Non-binary / other</option>
          <option value="prefer-not-to-say">Prefer not to say</option>
        </select>
      </p>

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

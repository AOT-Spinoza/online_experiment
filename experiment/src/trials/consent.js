// Informed consent screen.
//
// Runs as the very first interactive screen, before any task-related data
// is collected. Two buttons: "I agree" and "I do not agree". The response
// is logged to the saved data (`consent` field on a `consent` row) and
// `state.consentDeclined` is flipped if the participant declines, which
// the timeline's fail-gate uses to short-circuit to endSession().
//
// CLAUDE.md §3.3 step 3 — consent text supplied by the researchers.

import HtmlButtonResponse from '@jspsych/plugin-html-button-response';

const CONSENT_HTML = `
  <div style="text-align:left; max-width:680px; margin:0 auto; line-height:1.55; padding: 8px;">
    <h1 style="margin-top:0;">Informed consent</h1>

    <p style="color:#555; font-size:14px; margin-top:-8px;">
      Executive researchers: Shufan Zhang, Ningkai Wang, Tomas Knapen
    </p>

    <p>By agreeing to this informed consent form, you agree:</p>
    <ul>
      <li>to answer two questions about your age and gender;</li>
      <li>to perform a simple task;</li>
      <li>to complete a short questionnaire after finishing the experiment;</li>
      <li>to complete these tasks individually and seriously, online from
          home, in a quiet place, using a laptop or PC.</li>
    </ul>
    <p>In return, you will receive monetary compensation after completing
       all tasks.</p>

    <h3 style="margin-top:24px;">Further points of attention</h3>
    <ul>
      <li>There are no known risks associated with participation in this study.</li>
      <li>Participation is voluntary. You may withdraw from the study at any
          time without giving a reason.</li>
      <li>You will receive a random participant number, which will be linked
          to your questionnaires and online task data. Only the researchers
          listed above will have access to the key linking this random
          number to your personal data.</li>
      <li>Your personal data will be stored separately from the other data
          collected in this study.</li>
      <li>Your IP address will not be stored.</li>
      <li>Your data will be processed confidentially and shared with others
          only in anonymized form. Your personal data will never be shared
          with third parties.</li>
      <li>Data obtained in this study will be stored for up to 10 years after
          completion of the study.</li>
    </ul>

    <p>For further information about the study, please contact
       Tomas Knapen at
       <a href="mailto:t.knapen@vu.nl">t.knapen@vu.nl</a>.</p>

    <p style="margin-top:24px;">
      If you agree to participate, please click <strong>I agree</strong>.<br>
      If you do not agree, please click <strong>I do not agree</strong>.
    </p>
  </div>
`;

/** Build the consent trial. Sets `state.consentDeclined = true` if the
 *  participant clicks "I do not agree". The follow-up fail-gate in main.js
 *  reads that flag and routes to endSession('consentDeclined'). */
export function makeConsentTrial(state) {
  return {
    type: HtmlButtonResponse,
    stimulus: CONSENT_HTML,
    choices: ['I agree', 'I do not agree'],
    button_layout: 'flex',
    data: { trial_type_tag: 'consent' },
    on_finish(data) {
      const agreed = data.response === 0;
      data.consent = agreed ? 'agreed' : 'declined';
      if (!agreed) state.consentDeclined = true;
    },
  };
}

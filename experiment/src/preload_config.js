// Shared configuration for jsPsych's plugin-preload trial.
//
// We set this in one place so practice / qualification / main blocks all
// behave the same way on a preload failure: a participant-friendly error
// message that suggests refreshing or returning the study, and a console
// error with the failing URL so the researcher can diagnose (almost
// always either a CORS misconfiguration on the video host or the host
// being temporarily down).

import PreloadPlugin from '@jspsych/plugin-preload';

const ERROR_MESSAGE = `
  <div style="text-align:left; max-width:540px; margin:0 auto; line-height:1.5;">
    <h2>Couldn't load the videos.</h2>
    <p>This usually means a temporary network problem on our side or a
       brief blip in your connection. Please <strong>refresh the page in
       a minute</strong> and try again.</p>
    <p>If the message persists, please return the study on Prolific
       (so it doesn't affect your approval rate) and let the researchers
       know — your time is appreciated.</p>
  </div>`;

/** Build a preload-trial config for a given list of video URLs.
 *  Used by practice.js, qualification.js, and main_blocks.js so they
 *  share consistent error handling. */
export function preloadConfig({ videos, message, phase, blockIndex = null, jsPsych = null }) {
  const tag = `phase=${phase}` + (blockIndex != null ? `, block=${blockIndex}` : '');
  let _loadedCount = 0;
  return {
    type: PreloadPlugin,
    video: videos,
    show_progress_bar: true,
    auto_preload: false,
    message: message ?? '<p>Loading videos…</p>',
    error_message: ERROR_MESSAGE,
    continue_after_error: false,
    on_success: (url) => {
      _loadedCount += 1;
    },
    on_error: (url) => {
      // eslint-disable-next-line no-console
      console.error(`[preload] failed to load video (${tag}):`, url);
      // eslint-disable-next-line no-console
      console.error(
        `[preload] common causes:\n` +
        `  - CORS: video host's allowed origins don't include ` +
        `${typeof window !== 'undefined' ? window.location.origin : '<this origin>'}\n` +
        `  - Video host returning a status other than 200 (jsPsych's plugin silently\n` +
        `    drops responses with status not in {200, 0}; 404 is reported, others aren't)\n` +
        `  - stimuli.json has stale URLs — re-run pipeline/deploy.py and re-deploy`,
      );
    },
    data: {
      trial_type_tag: 'preload',
      phase,
      ...(blockIndex != null ? { block_index: blockIndex } : {}),
    },
    on_start() {
      // eslint-disable-next-line no-console
      console.info(
        `[preload] start (${tag}): ${videos.length} videos.`,
        videos.length > 0 ? `first=${videos[0]}` : '',
        videos.length > 1 ? `last=${videos[videos.length - 1]}` : '',
      );
    },
    on_finish(data) {
      // eslint-disable-next-line no-console
      console.info(
        `[preload] done (${tag}): success=${data.success}, ` +
        `loaded=${_loadedCount}/${videos.length}, ` +
        `failed_video=${(data.failed_video ?? []).length}, ` +
        `timeout=${data.timeout}`,
      );
      // The most useful invariant for debugging "block N has no videos":
      // verify every URL we asked for is now in jsPsych's video_buffer.
      // A missing buffer here is exactly the silent-failure mode that
      // the video plugin can't recover from — it'll then fall back to
      // a network <source src=...> fetch that may not finish in time.
      if (data.success && jsPsych != null) {
        const missing = videos.filter(
          (u) => !jsPsych.pluginAPI.getVideoBuffer(u),
        );
        if (missing.length > 0) {
          // eslint-disable-next-line no-console
          console.warn(
            `[preload] WARNING (${tag}): preload reported success but ` +
            `${missing.length}/${videos.length} URLs are NOT in the video buffer. ` +
            `These will fall back to a slow network fetch at trial time. First missing:`,
            missing[0],
          );
        }
      }
    },
  };
}

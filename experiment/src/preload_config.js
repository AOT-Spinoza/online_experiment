// Shared configuration for jsPsych's plugin-preload trial.
//
// We set this in one place so practice / qualification / main blocks all
// behave the same way on a preload failure: a participant-friendly error
// message that suggests refreshing or returning the study, and a console
// error with the failing URL so the researcher can diagnose (almost
// always either a CORS misconfiguration on the video host or the host
// being temporarily down).

import PreloadPlugin from '@jspsych/plugin-preload';
import CallFunction from '@jspsych/plugin-call-function';

import { endSession } from './prolific.js';

// Hard upper bound on how long any one preload trial may wait for all
// videos to load before giving up. 100 videos × ~400 KB = 40 MB; even
// on a 3 Mbps connection that takes ~110 s. We pick 120 s as a generous
// ceiling that catches genuinely hung connections (the original "wait
// forever" default trapped a participant for 30 min on a stuck XHR)
// without false-positive-ing a normal slow-but-working load.
const PRELOAD_MAX_LOAD_MS = 120 * 1000;

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
    // On failure (timeout or per-file error), let the trial advance so
    // the post-preload health-check below can route to a graceful
    // endSession() call rather than trapping the participant on a
    // dead-end "couldn't load" screen forever. continue_after_error +
    // health-check is much safer than continue_after_error: false,
    // which jsPsych's plugin-preload handles by replacing the page
    // body with `error_message` and leaving the timeline parked.
    continue_after_error: true,
    max_load_time: PRELOAD_MAX_LOAD_MS,
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

/** Decoder warm-up trial. Instantiates a hidden <video> element per URL,
 *  sets src to the preloaded blob URL, and waits for the `canplaythrough`
 *  event — i.e. waits until the browser confirms it has parsed the
 *  container, decoded the leading frames, and is ready to play through
 *  without buffering. Then disposes the element.
 *
 *  Why this exists:
 *
 *  jsPsych's plugin-preload uses XMLHttpRequest to fetch each video as a
 *  Blob and stores it via `URL.createObjectURL` in `video_buffers[url]`.
 *  That populates the *cache*, but it never instantiates a <video>
 *  element — so the browser's H.264 decoder pipeline stays cold. When
 *  the first trial of a block then creates its <video> with
 *  `src = blob:...; autoplay`, the browser has to cold-start the
 *  decoder, parse the container, and decode the leading frames before
 *  it can emit `ended`. On a tight per-trial safety timeout the first
 *  2-3 trials of a block can advance with no visible video — which was
 *  the symptom a pilot participant reported.
 *
 *  This trial closes the gap by forcing the decoder warm-up up-front,
 *  while a "Getting block ready…" message is still on screen. Once it
 *  finishes, every URL the upcoming block needs has been through a real
 *  <video> element and is decoder-ready.
 *
 *  Batched in groups of `perBatch` to avoid creating dozens of <video>
 *  elements at once (memory + concurrent-decoder limits, especially on
 *  underpowered devices). Each video gets `perVideoTimeoutMs` to fire
 *  canplaythrough; a missed event is logged and the trial continues
 *  rather than blocking the experiment — at worst the per-trial safety
 *  timeout will absorb it later.
 */
export function warmupVideosTrial(
  jsPsych,
  videos,
  { perBatch = 6, perVideoTimeoutMs = 4000, totalTimeoutMs = 30000, tag = '' } = {},
) {
  return {
    type: CallFunction,
    async: true,
    func: (done) => {
      if (!Array.isArray(videos) || videos.length === 0) {
        done();
        return;
      }

      const t0 = performance.now();
      let totalTimedOut = false;
      const totalTimer = setTimeout(() => {
        totalTimedOut = true;
        // eslint-disable-next-line no-console
        console.warn(
          `[warmup${tag ? ' ' + tag : ''}] total timeout (${totalTimeoutMs} ms) — ` +
          `proceeding to trials. Some videos may not be decoder-warm yet.`,
        );
        done();
      }, totalTimeoutMs);

      let idx = 0;
      let okCount = 0;
      let errCount = 0;
      let timedOutCount = 0;

      const finalize = () => {
        if (totalTimedOut) return;
        clearTimeout(totalTimer);
        const elapsed = Math.round(performance.now() - t0);
        // eslint-disable-next-line no-console
        console.info(
          `[warmup${tag ? ' ' + tag : ''}] done in ${elapsed} ms: ` +
          `${okCount} canplaythrough, ${errCount} errors, ` +
          `${timedOutCount} per-video timeouts (out of ${videos.length}).`,
        );
        done();
      };

      const runBatch = () => {
        if (totalTimedOut) return;
        if (idx >= videos.length) {
          finalize();
          return;
        }
        const batch = videos.slice(idx, idx + perBatch);
        idx += perBatch;

        const promises = batch.map((url) => new Promise((resolve) => {
          const v = document.createElement('video');
          // Off-screen + minimal box so the warm-up never paints over
          // the participant's view. We avoid `display:none` because
          // some browsers throttle decode on display:none elements.
          v.style.position = 'fixed';
          v.style.left = '-9999px';
          v.style.top = '-9999px';
          v.style.width = '1px';
          v.style.height = '1px';
          v.muted = true;
          v.preload = 'auto';
          const blob = jsPsych != null
            ? jsPsych.pluginAPI.getVideoBuffer(url)
            : null;
          v.src = blob || url;

          let settled = false;
          const cleanup = (outcome) => {
            if (settled) return;
            settled = true;
            v.oncanplaythrough = null;
            v.onerror = null;
            v.removeAttribute('src');
            try { v.load(); } catch (_) { /* ignore */ }
            try { v.remove(); } catch (_) { /* ignore */ }
            if (outcome === 'ok') okCount += 1;
            else if (outcome === 'error') errCount += 1;
            else if (outcome === 'timeout') timedOutCount += 1;
            resolve();
          };
          v.oncanplaythrough = () => cleanup('ok');
          v.onerror = () => {
            // eslint-disable-next-line no-console
            console.warn(`[warmup${tag ? ' ' + tag : ''}] video element error: ${url}`);
            cleanup('error');
          };
          setTimeout(() => cleanup('timeout'), perVideoTimeoutMs);
          document.body.appendChild(v);
        }));

        Promise.all(promises).then(runBatch);
      };

      runBatch();
    },
    data: { trial_type_tag: 'video_warmup' },
  };
}

/** Post-preload health check. Reads the most recent preload row from
 *  the jsPsych data store; if it reports `success: false` (timeout or
 *  per-file error), end the session via JATOS rather than letting the
 *  participant march into a block whose videos won't play.
 *
 *  Why this is on the timeline rather than inside the preload trial:
 *  jsPsych's plugin-preload doesn't expose a "ran-out-of-time, what
 *  next?" callback that gives us the jsPsych instance — we'd have to
 *  fork the plugin. A simple sentinel trial after preload is much
 *  cleaner and keeps the dependency direction clean.
 *
 *  We pass `'preload_failed'` as the endSession reason. Per-block data
 *  saved by `blockSave` trials in earlier blocks is already on the
 *  JATOS server; endSession's `jatos.endStudy(csv, false, …)` call
 *  flushes a final cumulative CSV (including the failing preload row)
 *  so the participant's progress isn't lost. */
function preloadHealthCheck(jsPsych, tag) {
  return {
    type: CallFunction,
    func: () => {
      const last = jsPsych.data
        .get()
        .filter({ trial_type_tag: 'preload' })
        .last(1)
        .values()[0];
      if (!last) return;
      // success is bool|null in plugin-preload; we treat anything other
      // than strictly true as a failure for participant-safety.
      if (last.success === true) return;
      const nFailed = Array.isArray(last.failed_video) ? last.failed_video.length : 'unknown';
      // eslint-disable-next-line no-console
      console.error(
        `[preload_health] FAILED (${tag}): success=${last.success}, ` +
        `timeout=${last.timeout}, failed_videos=${nFailed}. ` +
        `Ending the session gracefully — previous blocks are already saved on the server.`,
      );
      endSession(jsPsych, 'preload_failed');
    },
    data: { trial_type_tag: 'preload_health_check' },
  };
}

/** Convenience: build [preload, healthCheck, warmup] for a block. Drop
 *  all three into the timeline in order — preload populates the XHR-blob
 *  cache, healthCheck routes a failed preload to a graceful endSession,
 *  warmup primes the decoder for every URL in `videos`. */
export function preloadWithWarmup(opts) {
  const tag = `phase=${opts.phase}` +
    (opts.blockIndex != null ? `, block=${opts.blockIndex}` : '');
  const preload = preloadConfig(opts);
  const healthCheck = preloadHealthCheck(opts.jsPsych, tag);
  const warmup = warmupVideosTrial(opts.jsPsych ?? null, opts.videos, { tag });
  return [preload, healthCheck, warmup];
}

// Tiny helper module that centralises JATOS detection and integration.
//
// JATOS serves jatos.js at the same path as the study's static assets,
// so `<script src="jatos.js">` in index.html resolves to it when the
// bundle is served via /publix/<study-id>/start. When the bundle runs
// anywhere else (esbuild dev, GitHub Pages, Cloudflare Pages), that
// script 404s and `window.jatos` is left undefined. Runtime detection
// is therefore as simple as "does `window.jatos` exist?".
//
// All JATOS-specific behaviour in the rest of the codebase routes
// through these helpers so the conditional logic is in one place.

/** True if the bundle is running under JATOS (jatos.js has loaded). */
export function isUnderJatos() {
  return typeof window !== 'undefined' && typeof window.jatos !== 'undefined';
}

/** Run `fn` once jatos.js is fully initialised — or immediately when
 *  not under JATOS. Wraps `jatos.onLoad` so callers don't have to
 *  branch on the environment. */
export function whenJatosReady(fn) {
  if (isUnderJatos()) {
    window.jatos.onLoad(fn);
  } else {
    fn();
  }
}

// Stimulus loader: fetches stimuli.json (the public manifest) and produces
// per-phase, per-block trial lists.
//
// The public manifest is shaped:
//   {
//     "main":          [{stimulus_id, url}, ...],            ← real clips only
//     "catch":         [{stimulus_id, url}, ...],            ← instruction videos
//     "practice":      [{stimulus_id, url, direction}, ...],
//     "qualification": [{stimulus_id, url, direction}, ...]
//   }
//
// Direction labels are intentionally absent from main and catch (CLAUDE.md
// §3.9). Real and catch are split into separate arrays so the runtime can
// deliberately interleave N_catch + N_real per block — a uniform random
// shuffle over a merged pool puts ~0 catches in a 100-trial block in
// expectation, which is the bug we hit during testing.
//
// For local development without hosting the manifest may be empty. Callers
// detect this via `phaseHasStimuli(...)` and skip the affected phase with a
// warning rather than crashing.

import { STRUCTURE } from './config.js';


const MANIFEST_URL = './stimuli.json';

let _loaded = null;

/** Fetch stimuli.json once. Subsequent calls return the cached result.
 *
 *  Throws on fetch failure (network error, 404, CORS, malformed JSON, or
 *  a successfully-loaded file with an empty main pool). The caller is
 *  expected to catch and render a participant-visible error — silently
 *  falling back to empty arrays would let the timeline run to debrief
 *  without ever showing a real trial, which is what happened in the
 *  student's pilot. */
export async function loadStimuli() {
  if (_loaded) return _loaded;
  let raw;
  let res;
  try {
    res = await fetch(MANIFEST_URL);
  } catch (e) {
    throw new Error(
      `Could not reach ${MANIFEST_URL} (network or CORS error). ` +
      `Underlying error: ${e}`,
    );
  }
  if (!res.ok) {
    throw new Error(`Stimulus manifest HTTP ${res.status} at ${MANIFEST_URL}`);
  }
  try {
    raw = await res.json();
  } catch (e) {
    throw new Error(`Stimulus manifest at ${MANIFEST_URL} is not valid JSON: ${e}`);
  }

  _loaded = {
    main: Array.isArray(raw.main) ? raw.main : [],
    catch: Array.isArray(raw.catch) ? raw.catch : [],
    practice: Array.isArray(raw.practice) ? raw.practice : [],
    qualification: Array.isArray(raw.qualification) ? raw.qualification : [],
  };
  // eslint-disable-next-line no-console
  console.info(
    `[stimuli.js] manifest loaded — main: ${_loaded.main.length}, ` +
    `catch: ${_loaded.catch.length}, ` +
    `practice: ${_loaded.practice.length}, qualification: ${_loaded.qualification.length}`,
  );

  // Validate the manifest has the minimum stimuli for each phase the
  // experiment depends on. If any of these is empty we'd silently skip a
  // phase and the participant would race past instructions straight to
  // the debrief — which is the exact UX failure we hit during the
  // student's pilot. Throwing here lets main.js render a clean error
  // page instead.
  const minima = [
    ['main', 1],
    ['practice', STRUCTURE.practiceTrials - STRUCTURE.practiceCatchTrials],
    ['qualification', STRUCTURE.qualificationTrials],
  ];
  for (const [phase, min] of minima) {
    if (_loaded[phase].length < min) {
      throw new Error(
        `Stimulus manifest at ${MANIFEST_URL} loaded but the '${phase}' ` +
        `pool has only ${_loaded[phase].length} entries (need ≥ ${min}). ` +
        `This usually means a deployment was bundled before the manifest ` +
        `was regenerated. Re-run build_manifest.py with --base-url and ` +
        `--public-out, then rebuild.`,
      );
    }
  }

  return _loaded;
}

/** True if the given phase has any usable stimuli. Phases with no stimuli
 *  are skipped by their respective trial-builders. */
export function phaseHasStimuli(stimuli, phase, minimum = 1) {
  return Array.isArray(stimuli[phase]) && stimuli[phase].length >= minimum;
}

/** Sample N items from `pool` without replacement. Uses jsPsych's PRNG so
 *  the random stream is consistent with everything else in the experiment. */
function sampleN(jsPsych, pool, n) {
  if (n >= pool.length) return jsPsych.randomization.shuffle(pool.slice());
  return jsPsych.randomization.shuffle(pool.slice()).slice(0, n);
}

/** Build the practice list. Just shuffle and take the first
 *  STRUCTURE.practiceTrials items.
 *
 *  Note: 1 catch trial in practice (CLAUDE.md §3.4 Layer B) is sourced from
 *  the `main` array — caller passes it in via `catchPool`. */
export function buildPracticeList(jsPsych, stimuli) {
  const realPractice = sampleN(jsPsych, stimuli.practice, STRUCTURE.practiceTrials - STRUCTURE.practiceCatchTrials);
  // We can't tell from the public manifest which `main` entries are catch
  // trials — by design (§3.9). For practice, we just don't include any
  // catch trials at the manifest layer; instead we wire one in from the
  // built-in fallback. (This is acceptable because there's no production
  // data-collection cost to pulling a known catch entry.) See practice.js
  // for how it gets included.
  return jsPsych.randomization.shuffle(realPractice);
}

/** Build the qualification list. Just shuffle and take N. */
export function buildQualificationList(jsPsych, stimuli) {
  return sampleN(jsPsych, stimuli.qualification, STRUCTURE.qualificationTrials);
}

/** Build the per-block main lists for the entire session.
 *
 *  Returns an array of length STRUCTURE.mainBlocks. Each element is a
 *  shuffled list of `trialsPerMainBlock` entries, composed of:
 *
 *    - `realTrialsPerMainBlock` real entries from `stimuli.main`,
 *      partitioned across blocks so a clip never repeats within a session
 *    - `catchTrialsPerMainBlock` catch entries from `stimuli.catch`,
 *      sampled fresh per block (with replacement across blocks since the
 *      catch pool is small — typically 10 unique videos)
 *
 *  If either pool is too small for the configured cap, we degrade
 *  gracefully — using whatever is available and warning the console.
 */
export function buildMainBlocks(jsPsych, stimuli) {
  const realPool = jsPsych.randomization.shuffle(stimuli.main.slice());
  const catchPool = stimuli.catch || [];

  const N = STRUCTURE.mainBlocks;
  const realPerBlock = STRUCTURE.realTrialsPerMainBlock;
  const catchPerBlock = STRUCTURE.catchTrialsPerMainBlock;

  // Sanity warnings (don't crash; degrade to fewer trials).
  if (realPool.length < realPerBlock * N) {
    // eslint-disable-next-line no-console
    console.warn(
      `[stimuli.js] real pool (${realPool.length}) < ${N} × ${realPerBlock} = ${realPerBlock * N}; ` +
      `each block will get ${Math.floor(realPool.length / N)} real trials instead.`,
    );
  }
  if (catchPool.length < catchPerBlock) {
    // eslint-disable-next-line no-console
    console.warn(
      `[stimuli.js] catch pool (${catchPool.length}) < ${catchPerBlock} per block; ` +
      `each block will get ${catchPool.length} catch trials instead.`,
    );
  }

  const realThisBlock = Math.min(realPerBlock, Math.floor(realPool.length / N));
  const catchThisBlock = Math.min(catchPerBlock, catchPool.length);

  const blocks = [];
  for (let i = 0; i < N; i++) {
    // Real: take the next chunk from the partitioned pool.
    const real = realPool.slice(i * realThisBlock, (i + 1) * realThisBlock);
    // Catch: sample fresh per block. Across the 4 blocks the same catch
    // entries will recur (we have 10 unique × 5 per block = 20 picks),
    // which is fine — the participant just sees each instruction at most
    // a few times, and the response is well-defined regardless.
    const catches = jsPsych.randomization
      .shuffle(catchPool.slice())
      .slice(0, catchThisBlock);
    // Interleave randomly so catches aren't bunched at the start/end.
    const merged = jsPsych.randomization.shuffle([...real, ...catches]);
    blocks.push(merged);
  }
  return blocks;
}

# Arrow-of-Time Online 2-AFC Experiment — Plan

This repo will host an online psychophysics experiment in which Prolific participants view short video clips and judge whether each clip plays **forward** or **backward** (2-AFC). Source corpus: ~2,200 videos. Each is rendered in both directions, yielding ~4,400 stimuli. The work is split into two stages:

1. **Stimulus pipeline** — transcode source videos for the web, pre-render reversed copies, hash filenames so URLs leak no direction information, and upload to a CDN-backed object store.
2. **jsPsych experiment** — a v8 jsPsych task served as static files, recruiting participants via Prolific and saving data via DataPipe → OSF.

This document is the source of truth for design decisions. Update it when decisions change.

---

## 1. Background and references

- Task paradigm: arrow-of-time judgment from short natural videos. Closest published protocol: Meding et al., *Frontiers in Neuroscience* 2023 (3-s clips, 360 trials over 2 sessions, binary forward/reverse response, response within ~5 s; PMID via PMC10113813). A recent VLM-comparison benchmark (AoT-PsyPhyBENCH, arXiv:2510.26241) uses the same human protocol as a baseline.
- Use those papers' choices as defaults unless we have a reason to deviate.

## 2. Stage 1 — Stimulus pipeline

### 2.1 Goals
- Reduce per-file size aggressively so the experiment streams smoothly on consumer connections.
- Produce a forward and a reverse render of each source clip (browsers cannot reverse video playback at runtime).
- Anonymize filenames so a scraping bot cannot infer the correct answer from the URL.
- Produce a phase-split **public manifest** (direction labels for practice/qualification only, never for main) and a **private manifest** with direction labels for everything (gitignored, used only by offline scoring). See §2.4.

### 2.2 Encoding choices (defaults)
- Container/codec: **MP4 / H.264 (libx264)** — broadest browser support including Safari/iOS. Drop the audio track entirely (`-an`) — audio is irrelevant for the task and reversed audio is unnatural. No HEVC/AV1 in the primary path: HEVC fails in Firefox, AV1 has spotty older-Safari support; we will not maintain multi-codec fallbacks for v1.
- Resolution: **480p short side** (e.g., `-vf scale=-2:480`), keep aspect ratio. Reduce further (360p) if file sizes are still too large after a sample run.
- Quality: **CRF 28**, `-preset slow` for a one-time encode. Re-evaluate after a 50-clip pilot — drop to CRF 26 if quality looks degraded, raise to 30 if size matters more.
- Streaming: `-movflags +faststart` so playback can begin before full download.
- Frame rate: keep source fps; do not duplicate frames.
- Pixel format: `-pix_fmt yuv420p` for universal browser decode.

### 2.3 Source layout & reverse rendering
- Source clips are confirmed **2.5 s, 60 frames at 24 fps, uniform across the corpus**. No trim or pad logic needed in the pipeline.
- **The forward and reverse renders are pre-existing**: the researcher delivers both directions as separate files. The pipeline therefore does NOT use ffmpeg's `reverse` filter; it just transcodes whatever it finds.
- Source location: `videos/rescaled_final/` (gitignored).
- Filename convention: **`NNNN_fw.mp4`** (forward) and **`NNNN_rv.mp4`** (reverse), where `NNNN` is a 4-digit zero-padded source ID. Direction is recoverable from the filename suffix; the transcoder is agnostic about direction and just preserves filenames in the staging copy and records `original_path` in the TSV.

### 2.4 Filename hashing & manifests

- Filenames: random UUIDv4 (or a 16-byte random hex string). Two independent IDs per source — one for the forward render, one for the reverse render. **Both IDs are random; an attacker cannot tell from the filename which is which.**
- Manifests are split by phase, so structure enforces the bot-resistance rule (no main-task labels on the client):

  - `pipeline/manifest_public.json` — what ships with the experiment:
    ```json
    {
      "main":          [{"stimulus_id": "...", "url": "..."}],                            // NO direction; real clips only
      "catch":         [{"stimulus_id": "...", "url": "..."}],                            // NO labels; runtime picks from here
      "practice":      [{"stimulus_id": "...", "url": "...", "direction": "forward"}],   // labels OK
      "qualification": [{"stimulus_id": "...", "url": "...", "direction": "forward"}]    // labels OK
    }
    ```
    `main` and `catch` are kept in separate arrays so the runtime can deliberately mix 95 real + 5 catch per block — a uniform random shuffle over a merged pool gives ~0 catches per 100-trial block in expectation. Within each, entries are still just `{stimulus_id, url}`.
    Practice and qualification carry direction labels because the live feedback gate needs them. Their clips come from a **separate pool** that **never appears in main blocks** (§3.4), so there's no leak.

  - `pipeline/manifest_private.json` — `[{stimulus_id, source_file, type, direction, expected_confidence?}, ...]` for **all** stimuli. `type ∈ {'main','catch','practice','qualification'}`. **Gitignored** (or kept in a separate private repo). Used only by `analysis/score.py` to compute correctness for main trials and catch-trial pass rate.

- **Familiarization stimuli** (§3.4 Layer A) need no separate label — the visible instruction in the stimulus tells the participant what to press. They live in the experiment code/static assets, not the manifest.

- The private manifest is the one piece of state that, if leaked, breaks the bot-resistance design for main trials. Treat it like a secret. It must never be served from the experiment's host.

### 2.5 Hosting

**Status: deferred** — researcher is contacting university IT about institutional hosting/download options. Lock this section once that comes back.

Default if uni doesn't provide something suitable: **Cloudflare R2** (free egress, S3-compatible, easy CORS, ~4,400 files × ~300–500 KB ≈ 1.5–2 GB total — inside the free tier as of writing).

Whatever we end up with, it must:
- Serve `video/mp4` with correct MIME and `Accept-Ranges: bytes` (for streaming/seek).
- Allow CORS `GET` from the experiment's origin only (not `*`). The experiment HTML/JS will be cross-origin from the videos — this is the key security boundary.
- Have stable URLs we can list in `manifest_public.json`.

Alternatives that came up:
- AWS S3 + CloudFront — works, egress metered (potentially significant at scale).
- Backblaze B2 — cheap, but S3-compatible API has CORS quirks (`access-control-allow-origin` not supported with auth).
- GitHub Pages / LFS — file/repo/bandwidth limits make 2 GB of streamed video infeasible.
- A uni-provided S3-compatible store, WebDAV, or a campus CDN — preferred if available and CORS-able.

### 2.6 Pipeline scripts (to build)
Planned layout under `pipeline/`:
- `transcode.py` — walks a source directory, transcodes every video to web-friendly MP4, writes paper-trail copies to `staging/` and hashed-filename deployment copies to `staging_hashed/`. Emits `secrets/hash_map.tsv`. (Built; see code.)
- `generate_catch_trials.py` — generates the 10 catch-trial instruction videos (§2.7) into `videos/rescaled_final/` using ffmpeg's `drawtext` filter. They go through `transcode.py` like any other source.
- `build_manifest.py` — reads `hash_map.tsv` and parses `original_path` to derive metadata (phase, direction, catch-vs-real, expected confidence). Emits the phase-split `manifest_public.json` (committed) and `manifest_private.json` (gitignored).
- `upload.py` — uploads `staging_hashed/` to the chosen host. Implementation details depend on §2.5.
- `verify.py` — sanity-check pass: every public-manifest URL returns 200, content-length is plausible, MIME is `video/mp4`, CORS headers permit the experiment origin.
- `requirements.txt` — `tqdm`, plus `boto3` + `python-dotenv` once we wire `upload.py`. ffmpeg is a system dependency.

Hosting credentials live in a `.env` file at the repo root (gitignored). Exact variable names depend on §2.5; for an S3-compatible target they'll look like `BUCKET_ENDPOINT`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY`, `BUCKET_NAME`, `PUBLIC_BASE_URL`.

### 2.7 Catch-trial generation

To probe attention and bot vigilance we splice **instruction videos** into the main task — 2.5-s MP4s that show on-screen text such as:

```
Press FORWARD
Then press 3
```

A participant paying attention reads the text and complies on both axes (direction key + confidence number). An inattentive participant or a bot scraping URLs cannot tell, from the URL alone, that this trial is a catch trial — the URL is a hashed random ID indistinguishable from any real clip's URL.

**Source files**: 10 unique combinations (2 directions × 5 confidence levels), one variant each for v1:
```
catch_fwd_1.mp4  ←  "Press FORWARD\nThen press 1"
catch_fwd_2.mp4
…
catch_rv_5.mp4   ←  "Press BACKWARD\nThen press 5"
```
(Colons inside drawtext values get parsed as filter-option separators by ffmpeg — the textual style avoids them.)

Generated by `pipeline/generate_catch_trials.py` directly into `videos/rescaled_final/` so they share the rest of the pipeline — `transcode.py` then re-encodes them to the same target settings as real clips and gives them hashed names alongside everything else. White text on a dark grey (#1a1a1a) background, 480p, 24 fps, 60 frames, audio dropped, `+faststart`.

**Manifest treatment** (built by `build_manifest.py`):

- `manifest_public.json` — catch entries are interleaved into the `main` array as plain `{stimulus_id, url}` pairs, **no `is_catch` flag exposed to the client**. Structurally identical to real-clip entries.
- `manifest_private.json` — full metadata: `{stimulus_id, source_file, type: 'main'|'catch', direction, expected_confidence?}`.

**Naming convention recap** (so `build_manifest.py` can derive metadata from `original_path`):

| Pattern                 | Phase            | Type   | Notes                              |
|-------------------------|------------------|--------|------------------------------------|
| `NNNN_fw.mp4`           | main             | real   | direction = forward                |
| `NNNN_rv.mp4`           | main             | real   | direction = backward               |
| `catch_fwd_C.mp4`       | main             | catch  | direction = forward, conf = C ∈ 1–5|
| `catch_rv_C.mp4`        | main             | catch  | direction = backward               |
| `practice/<name>_fw.mp4`| practice         | real   | hand-picked obvious clip (§3.4)    |
| `practice/<name>_rv.mp4`| practice         | real   |                                    |
| `qualification/<name>_fw.mp4` | qualification | real | hand-picked obvious clip (§3.4) |
| `qualification/<name>_rv.mp4` | qualification | real |                                  |

### 2.8 Obvious-clip processing pipeline

Practice + qualification stimuli must come from a pool that **never appears in the main blocks** (no leak from practice into the main experiment). They're sourced from `pipeline/source_obvious/` and processed by `pipeline/process_obvious.py`, which is a separate pipeline from the main-corpus `transcode.py`.

**Why a separate script**: the main corpus is delivered pre-trimmed (uniform 2.5 s / 60 frames @ 24 fps), so `transcode.py` only does scaling + encoding. The new obvious clips arrive at varied resolutions and frame rates and need to be trimmed, frame-rate-normalised, and reversed. `process_obvious.py` handles all of this in a self-contained pipeline.

**Stages** (each visible under `pipeline/source_obvious/`):

```
pipeline/source_obvious/
├── originals/              # raw input videos (any res, any fps, any container)
├── trimmed/                # 2.5-s clips at original resolution, CRF 18
│                           # (high-quality intermediate; useful to verify trim window)
└── processed/
    ├── practice/           # final 480p / 24 fps / CRF 28 (forward + reverse)
    └── qualification/      # final 480p / 24 fps / CRF 28 (forward + reverse)
```

**Trim window**: by default the script picks the **center 2.5 s** of each input (uses `ffprobe` to read duration, computes `(D−2.5)/2` as start). Override globally with `--start-sec X`, or per-file via a JSON sidecar at `originals/start_times.json`.

**Stem extraction**: each input's leading numeric prefix is used as the clip's ID (e.g. `12314192` from `12314192_3840_2160_30fps.mp4`). 7–8-digit IDs don't collide with the main corpus's 4-digit IDs.

**Practice/qualification split**: alphabetical halves by default (first half → practice, second → qualification). Override by pre-organising into `originals/practice/` + `originals/qualification/` subdirs.

**Hashing**: `process_obvious.py` does its own hashing — it generates a fresh random hex ID per processed file, copies into `pipeline/staging_hashed/<hash>.mp4`, and appends entries to `secrets/hash_map.tsv` with `original_path` of the form `practice/<stem>_fw.mp4` or `qualification/<stem>_rv.mp4`. `build_manifest.py` then picks them up directly into `public['practice']` / `public['qualification']`.

**Bot-resistance posture**: these clips are NEVER in `public['main']`. A bot scraping the bundle that knows which clips are practice/qualification gains nothing on the main task — those are entirely different stimuli with different hashes.

## 3. Stage 2 — jsPsych experiment

### 3.1 Versions and dependencies
- **jsPsych v8.x** (current stable).
- Core plugins (npm under `@jspsych/`):
  - `plugin-preload` — batch media preloading.
  - `plugin-video-keyboard-response` — main 2-AFC trial. Keyboard responses (← / →) keep hands off the mouse; chosen over `video-button-response` because the participant initiates each trial via spacebar (§3.3) — switching to mouse for response would add hand-shift RT noise on every trial.
  - `plugin-html-keyboard-response` — instructions, the "press SPACE to continue" prompts, simple text screens.
  - `plugin-html-button-response` — block-end summary screens with Continue / Finish buttons (these are not time-critical; clicks are fine).
  - `plugin-fullscreen` — enter/exit fullscreen.
  - `plugin-browser-check` — block mobile/tablet, enforce minimum viewport.
  - `plugin-survey-html-form` — optional free-text debrief (no demographics; see §3.3 step 13).
  - `plugin-call-function` — sequenced async work around saving.
- Contrib plugin: **`@jspsych-contrib/plugin-pipe`** — sends data to DataPipe for storage on OSF. No backend server needed.
- Bundling: ship as plain ESM with a small build step (`esbuild`) producing one `experiment.bundle.js`. Avoids a heavy framework.

### 3.2 Hosting the experiment HTML/JS
- Static files only. Default: **GitHub Pages** (free, fine for the request volume) or **Netlify**/**Cloudflare Pages** if we want preview deploys per branch. Or a uni-hosted static endpoint if convenient.
- Videos are on a different origin from the experiment HTML/JS — **this is the only cross-origin hop and the reason CORS on the video host must be set correctly** (§2.5). Locking the video-host choice (§2.5) determines what origin the experiment HTML must be served from for CORS to allow video loads.

### 3.3 Block-structured trial design

A typical session runs ~45 minutes. **4 main blocks** of 100 trials each, each trial participant-initiated via the spacebar. A soft **1-hour session cap** acts only as a runaway-session safety net (a participant who falls asleep on the participant-paced confidence prompt is stopped automatically; in normal use no one hits it).

**Per-trial timing**
- "Press SPACE to start the next trial" prompt — participant-paced (~0.5–2 s). Spacebar is the only key accepted here. **Every trial starts on a spacebar press**, no exceptions.
- Video plays for **2.5 s** (`response_allowed_while_playing: false`).
- **Direction response window**: 1 s. Keys: **← (ArrowLeft) = backward, → (ArrowRight) = forward**. Meaning is intrinsic to the arrow; no per-participant counterbalancing needed, but `response_key` is logged literally so analysis is unambiguous.
- **Confidence prompt**: shows a horizontal 1–5 scale with anchor labels: *guess · somewhat unsure · unsure · somewhat sure · certain*. Keys 1–5 on the top number row. **No timeout — participant-paced**. If direction was missed (no key in the 1-s window), the confidence prompt is **skipped** and `confidence = null`.
- ~500 ms blank ITI.
- Average wall-time per trial: ~5.5–6 s including spacebar + confidence pace.
- `direction_rt` measured from the video's `ended` event.
- `confidence_rt` measured from the confidence prompt's onset.

**Target counts**
- 4 main blocks × **100 trials = 400 main-task trials per session** (revised down from 480 to absorb the confidence prompt within ~45 min wall time).
- 95 real + 5 catch trials per block, randomly interleaved (§3.5 / §2.7).
- ~10 min per main block.
- Total 4 × 10 = 40 min main-task + ~5 min for landing/instructions/familiarization/practice/qualification/breaks ≈ 45 min wall time.

**Phases, in order**
1. **Landing & metadata (silent)** — on page load, before any UI: capture `PROLIFIC_PID`, `STUDY_ID`, `SESSION_ID`, plus `session_start_ms = Date.now()`. Stored via `jsPsych.data.addProperties(...)` so every saved row carries them. The PID falls back to `LOCAL_<random>` for local dev.
2. **Informed consent** — institutional consent text shown via `plugin-html-button-response`. Two buttons: **I agree** / **I do not agree**. Response logged on a `consent` row (`data.consent ∈ {'agreed','declined'}`). Decline → `endSession('consentDeclined')` (graceful exit, completion code `PLACEHOLDER_NOCONSENT` in §3.8). Implemented in `experiment/src/trials/consent.js`.
3. **Welcome** — short page; shows the captured PID for traceability. Spacebar to proceed.
4. **Browser/device check** — desktop only, modern Chromium/Firefox/Safari, viewport ≥ 1024×600. Polite reject otherwise with no completion code (so they can return the study). **TODO**: plugin pulled in via package.json; not yet wired into the timeline.
5. **Layer A intro + Layer A familiarization (~8 trials, §3.4)** — synthetic HTML instruction stimuli covering direction key, confidence key, and combined responses. No real-clip ground truth involved.
6. **Task intro** — full task explanation including direction + confidence keys, the catch-trial format, and the catch-trial bonus disclosure (per the consent commitment).
7. **Layer B — Practice (~12 trials, with feedback, §3.4)** — real obvious clips from `pipeline/source_obvious/`, ground truth shipped, per-trial "Correct" / "Not quite" feedback. **These clips are processed via `process_obvious.py` (§2.8) and live in a separate pool from the main corpus — they never appear in main blocks.** Plus **2 catch-trial demos** so participants encounter the catch format twice before main; spread at thirds of the layer so they're never back-to-back.
8. **Layer C — Qualification (~10 trials, gate, §3.4)** — disjoint obvious clips + **1 catch trial** (so the format also lives in the gated phase), no per-trial feedback, ≥ 75% correct on direction (catch row excluded from the gate calculation) to proceed. Failure ends gracefully with a Prolific completion code that pays for time spent (excluded from analysis, **not "rejected" on Prolific**).
9. **Main intro** — explicit "the experiment starts now / your responses count" message.
10. **Main blocks (4 × 100 trials)** — 95 real + 5 catch interleaved (§3.5). `response_allowed_while_playing: false`; direction RT measured from the response prompt's onset. **No ground truth on the client** for real or catch trials in main — see §3.9.
11. **Block-end screen** — summary: trials completed, median RT, elapsed time, accrued bonus. **No accuracy shown for main blocks** (no client-side ground truth — see §3.7). Single **Continue** button (forward-only flow; the production version no longer offers a between-block early-exit). Loop terminates after the 4th block or on hitting the 1-hour runaway-session cap.
12. **Mandatory rest** — between blocks 2 and 3, a forced ≥30 s break with a countdown.
13. **Final survey** — two optional free-text fields ("anything unusual?" + general comments). **No demographics collected.** Earlier drafts included age + gender; these were removed to keep the dataset free of any participant-typed personal information, which simplifies data-management and ethics handling. The Prolific PID is still attached to every row (needed for payment + per-participant joins), but no demographic data is asked for or stored.
14. **Save data** via `jsPsychPipe` (§3.8). Must complete before redirect.
15. **Redirect** to Prolific completion URL.

**Implementation note** — each block is wrapped in a `conditional_function` that checks `elapsed < STRUCTURE.maxSessionMs` (currently 1 hour). The block-end Continue button always advances; the cap is the only programmatic short-circuit.

**Payment structure** — base payment covers landing → qualification (~5 min). Each completed main block pays a Prolific bonus. Tune for ≥ £8–10/hr equivalent. Exact amounts: open question — see §6.

### 3.4 Pre-experiment screening — three layers

Three pre-task gates, ordered from interface-only to task-only. Each layer is leak-bounded (see §3.9).

**Layer A: Interface familiarization (~8 trials, no real stimuli)**
- Synthetic instruction trials via `plugin-html-keyboard-response` (HTML text, no video file). Three sub-types, mixed and shuffled:
  - **Direction key** (×2): "Press → for FORWARD" / "Press ← for BACKWARD"
  - **Confidence key** (×2): "Press 3" / "Press 5" — teaches the number-row response
  - **Combined** (×4): "Press → for FORWARD, then press 4" — full per-trial response shape
- Tests: can the participant read instructions, do they understand the direction *and* confidence mappings, are they actually present at the keyboard.
- **No real-clip ground truth involved.** The "correct" response is whatever the visible instruction names.
- Why HTML rather than pre-rendered MP4: simpler pipeline (no extra ffmpeg job), faster to iterate on instruction wording, response modality is identical to main trials.
- Failure mode: ≥ 2 consecutive errors → polite end-of-session screen, completion code paying for time spent.

**Layer B: Task practice (~12 trials, with feedback)**
- Real obvious clips from `pipeline/source_obvious/`, processed via `process_obvious.py` (§2.8). These are externally-sourced clips — they are **not** in the main corpus.
- **Plus 2 catch trials** (instruction videos) so participants encounter the catch-trial format twice before main blocks (§2.7). `STRUCTURE.practiceCatchTrials = 2` controls the count. The catches play from `stimuli.catch` and show no feedback (the public manifest doesn't carry catch direction/expected_confidence). Inserted at spread positions so they're never back-to-back.
- Each trial includes the full direction + confidence response.
- Ground truth **is** shipped for the obvious clips because per-trial direction feedback requires it. Feedback shows direction correctness only ("Correct" / "Not quite") — we deliberately don't comment on the confidence rating.
- Practice clips never appear in main blocks because they aren't in the main pool to begin with.

**Layer C: Qualification (~10 trials, gate)**
- Real obvious clips from the same `pipeline/source_obvious/` pool, **disjoint from practice** (split decided by `originals/practice/` + `originals/qualification/` subdirs, or by alphabetical halves if flat).
- **Plus 1 catch trial** (`STRUCTURE.qualificationCatchTrials = 1`) so the catch format also lives in the gated phase — participants who breeze through Layer B's catches still see one more before the main blocks "count for real." The catch row is **tagged `is_qualification_catch: true` and excluded from the gate's accuracy fraction** (its expected response is offline-scored against the private manifest, not on the client, so including it would always be treated as wrong).
- Each trial includes the full direction + confidence response.
- Ground truth shipped (needed for the gate decision); no per-trial feedback shown.
- Gate is on **direction accuracy on obvious clips only**: ≥ 75% correct to proceed (default; revisit after pilot). Confidence is recorded but not gated.
- Failure: graceful exit with completion code paying for time spent.

**Obvious-clip set: practical requirements**
- Source videos go in `pipeline/source_obvious/originals/`. Any resolution / fps / container — `process_obvious.py` (§2.8) normalises them to 480p / 24 fps / 2.5 s and produces both forward and reverse renders.
- **Minimum**: ≥ 22 distinct source clips (12 practice + 10 qualification, disjoint). Each source produces 2 files (fw + rv).
- **Preferred**: 20–35 source clips so practice + qualification can rotate per participant — limits the value of any leaked answer key.
- Visually consistent with the main corpus (natural everyday actions; not novelty stimuli).
- Optional `originals/start_times.json` sidecar for per-file trim windows when the obvious moment isn't at the source's center.

### 3.5 Sampling design (between-subjects)
- 4,400 real stimuli is far more than one participant can see; ~380 real trials per participant ≈ 9% of the pool (the remainder of each block's 100 trials is filled by catch trials, see below).
- **Within-block composition**: each main block is **95 real + 5 catch**, with the 95 real trials counterbalanced **47 forward + 48 backward** (or 48/47, alternating across blocks within a participant for exact 50/50 over the full session). Order is random within the block.
- **Catch-trial selection**: 5 catch trials sampled from the pool of 10 unique catch videos (§2.7) per block, without replacement *within* a block. Across blocks the same catch entries can recur — that's fine because there are only 10 unique ones.
- **Across the population**: use DataPipe's balanced condition-assignment endpoint to hand each session the next slot in a sequence; the slot determines which 380-real-trial sub-list (= 4 blocks × ~95 real) the participant gets. Alternative: pre-generate N participant lists offline and round-robin via DataPipe condition assignment.
- A clip should never appear in both directions for the same participant (would let them use prior viewing as a cue). Forward and reverse versions of the same source go to different sub-lists.
- Across the experiment population, each clip should be seen at least *N* times in each direction (target *N*: open question — §6).

### 3.6 Preloading strategy
- Total pool is too large to global-preload. Preload is per-block: just before main-block *k* starts, preload its 100 clips.
- Use the `preload` plugin with `auto_preload: false` and an explicit `video: [...]` list for the upcoming block.
- `show_progress_bar: true` for block-level preloads.
- 100 files × ~400 KB ≈ 40 MB per block — typically 8–15 s to preload on a home connection. Surface a "still loading… your connection may be slow" message after 30 s, and a graceful abort path if it stalls past 90 s.
- Preloading does not work when `index.html` is opened from disk; only when hosted.

### 3.7 Online analysis and live feedback

We compute correctness in the browser **only for familiarization, practice, and qualification**. For main blocks, no ground truth is on the client (§3.9), so live accuracy cannot be — and is not — shown. Other engagement metrics still are.

**Per-trial logged fields** (on top of jsPsych's built-in `response`/`rt`):
- `stimulus_id` — anonymous ID from the public manifest (or `null` for HTML-rendered familiarization trials).
- `phase` — `'familiarization'` | `'practice'` | `'qualification'` | `'main'`.
- `block_index` — 0..3 for main blocks.
- `response_key` — the literal direction key pressed (`'ArrowLeft'` or `'ArrowRight'`).
- `response_direction` — derived from the key (← = backward, → = forward); recorded explicitly so analysis doesn't have to remember the mapping.
- `direction_rt` — RT to the direction key, measured from the response prompt's onset (= the moment direction keys become enabled, immediately after the video's `ended` event).
- `confidence` — integer 1–5 or `null` (null when direction was missed, since the confidence prompt is then skipped). NB: the confidence value is recorded on a separate `trial_type_tag: 'confidence'` row at runtime; `analysis/load_data.py`'s `merge_confidence_into_stimulus` joins it onto the canonical stimulus row at load time.
- `confidence_rt` — RT to the confidence key, measured from confidence-prompt onset. `null` if confidence was skipped.
- `play_completed` — did the video reach the `ended` event before the response window opened.
- `tab_blurs_during_trial` — count of `visibilitychange` blurs while the trial was active.
- `correct` — boolean for direction. **Set only for familiarization, practice, qualification.** `null` for main trials.

**Global metadata** (attached via `addProperties`, present on every saved row):
- `PROLIFIC_PID` — from the URL, or `LOCAL_<rand>` for local dev.
- `STUDY_ID`, `SESSION_ID` — from Prolific's URL params (when present).
- `session_start_ms` — `Date.now()` at session start. Lets the analysis loader disambiguate multiple sessions saved to the same `localStorage` / DataPipe folder by picking the most-recent `session_start_ms`.
- `consent` — `'agreed'` | `'declined'`, recorded on the dedicated consent row (`trial_type_tag: 'consent'`). For declined sessions there are no other rows past this one because the timeline aborts immediately.

**Computed on-device**
- After familiarization: pass/fail. ≥ 2 consecutive errors → polite session-end with completion code for time spent.
- After qualification: accuracy → gate decision (≥ 75% to proceed).
- After every main block: median RT, lapse rate (% trials with RT < 250 ms or > response-window cap), tab-blur count, trials completed, elapsed time, accrued bonus.

**Shown to the participant at the end of each main block**
- Trials completed in this block + cumulative.
- Median RT (a soft engagement indicator without revealing correctness).
- Elapsed time and accrued bonus.
- Encouragement + Continue / Finish.

Side benefit of not showing accuracy: removes the strategy-adaptation risk we'd discussed previously (participants switching to always-forward if they thought they were below chance).

**Exported for offline analysis** (in the saved CSV)
- All per-trial rows (with `correct = null` for main trials; recomputed offline).
- Per-block summary rows tagged `trial_type: 'block_summary'`.
- Global metadata: PIDs, user-agent, viewport, platform, completion path (`finished_normally` | `cap_45min` | `qualification_failed` | `familiarization_failed` | `participant_finished_early`), total session `tab_blurs`.

**Offline analysis** (`analysis/score.py`)
- Joins responses with the **private manifest** to compute `correct` for main trials.
- Cross-checks: client-reported `correct` for practice/qualification matches the private-manifest computation (catches bugs or tampering).
- Signal-detection metrics: d′, criterion (forward-bias measure).
- Per-stimulus difficulty aggregated across participants.
- Exclusion pipeline: failed familiarization, failed qualification, RT outliers, no-forward-bias signature, low-variance RT distributions, excessive tab-blurs.

### 3.8 Data saving
Pattern (v8 + `@jspsych-contrib/plugin-pipe`):
```js
const filename = `${jsPsych.data.getURLVariable('PROLIFIC_PID') ?? jsPsych.randomization.randomID(10)}.csv`;
const save_data = {
  type: jsPsychPipe,
  action: 'save',
  experiment_id: 'XXXXXXXXXXXX',     // from DataPipe dashboard
  filename,
  data_string: () => jsPsych.data.get().csv(),
};
```
- Place `save_data` immediately before the Prolific redirect trial so saving completes first.
- **Save partial data per main block** via a second `jsPsychPipe` trial (action: `save`, distinct filename per block, e.g. `${PID}_block${k}.csv`). Protects against last-minute network failures losing the whole session. Analysis joins per-block files by `PROLIFIC_PID`.
- Belt-and-braces: also write `jsPsych.data.get().json()` to `localStorage` after each block, so even a DataPipe outage doesn't lose everything.
- DataPipe + OSF accounts must be created before deployment — researcher to set up.

### 3.9 Bot/agent resistance — final posture

**Decision**: ground truth for **main** trials does **not** ship to the client. Live feedback for main blocks is limited to engagement metrics (RT, time elapsed, bonus). Practice and qualification do ship labels, but those clips never appear in main blocks, so the leak is bounded.

**Defenses in place**
1. **Random hashed filenames** (§2.4) — URLs leak no information.
2. **Manifest split** — `manifest_public.json` ships direction labels only for `practice` and `qualification`; the `main` array contains only `{stimulus_id, url}`. Structurally enforced.
3. **No DOM hints** — no class names, alt text, ARIA labels, data attributes, or CSS selectors that name direction anywhere.
4. **Cross-origin video host** (§2.5/§3.2) — videos and experiment HTML are on different origins; naive scrape-by-URL approaches don't even get the videos without CORS-respecting requests.
5. **Familiarization is leak-free** — instruction text is in the visible stimulus itself; the client doesn't need a separate label.
6. **Practice/qualification leak is bounded** — only ~22–40 obvious clips ever ship with labels, and they never appear in main blocks. A bot that scrapes those labels gains nothing on the 4,400 main stimuli.
7. **Catch trials interspersed in main blocks** (§2.7). The public manifest exposes a separate `catch` array (so the runtime can deliberately mix 95 real + 5 catch per block). A bot scraping the bundle therefore knows which 10 stimuli are catches — but it still has to **decode each catch video to read the on-screen instruction** in order to comply (direction + confidence are different per catch). The 4358 real-clip stimuli stay unidentifiable. Catch-trial performance feeds two **separate** decisions (see "Behavioral filtering" below): the **catch-direction** pass rate gates analysis inclusion, while the stricter **catch-full** pass rate (direction AND confidence) governs the payment bonus communicated to participants upfront.

**Behavioral filtering (offline)**
1. Failed familiarization or qualification → exclude.
2. **Catch-direction pass rate < 80% → exclude.** The attention gate is the catch *direction* pass rate, not catch full-pass. The catch's confidence instruction ("…then press 3") conflicts with the response mapping participants are trained on for 400 trials (number key = their *own* confidence); an engaged-but-hasty participant reads the direction correctly but rates confidence normally instead of obeying the override. Gating on direction cleanly separates genuine autopilot (can't fake 16/20 on a 50/50 direction split) from this confidence-key slip. The **bonus** is a separate decision: contingent on catch *full*-pass (direction AND confidence) ≥ 80%, the rule disclosed to participants — so a participant can be analysis-included but bonus-ineligible.
3. Median main-block direction RT outside [250 ms, response-window cap] → exclude.
4. RT distributions with implausibly low variance → flag.
5. Forward-bias absence → flag (the human bias is large and robust per Meding et al.; bots rarely match it).
6. High `tab_blurs_during_trial` count → flag (consistent with running an external model in another tab).
7. Sustained 100% accuracy on main blocks across hundreds of trials → flag (even on natural clips, some are genuinely ambiguous).
8. Confidence-direction calibration completely flat (e.g., constant confidence regardless of accuracy) → flag.

**Not defended against**
- A determined human-in-the-loop attacker who watches every video. Prolific reputation gating absorbs most of that risk.

**Fallback (if pilot data shows problems)**
- Move main-task scoring to a Cloudflare Worker oracle: client posts `{stimulus_id, response, session_token}`; worker returns only `{correct: bool}` after the response is recorded server-side. Per-session rate limits (e.g., 1 request / 2 s, 1 lookup per stimulus) prevent bulk harvesting. Out of scope for v1; architecture leaves room.

## 4. Repository layout

```
online_experiment/
├── CLAUDE.md                    # this file
├── README.md                    # short, public-facing
├── .gitignore                   # ignores .env, secrets/, videos/, staging dirs, analysis/data/
├── videos/
│   └── rescaled_final/          # MAIN-CORPUS source MP4s (gitignored). Naming: NNNN_fw.mp4 / NNNN_rv.mp4
├── pipeline/
│   ├── transcode.py             # main-corpus transcode (videos/rescaled_final → staging/ → staging_hashed/)
│   ├── generate_catch_trials.py # builds the 10 catch instruction-text MP4s
│   ├── process_obvious.py       # NEW: trim+reverse+transcode external obvious clips (§2.8)
│   ├── build_manifest.py        # emits manifest_public.json + secrets/manifest_private.json from hash_map.tsv
│   ├── dev_link.py              # local-dev convenience: symlink + run build_manifest with relative URLs
│   ├── upload.py                # implementation depends on §2.5
│   ├── verify.py
│   ├── requirements.txt
│   ├── staging/                 # main-corpus paper-trail transcodes (gitignored)
│   ├── staging_hashed/          # deployment-ready hashed-name copies for ALL clips (gitignored)
│   ├── source_obvious/          # external obvious clips for practice + qualification
│   │   ├── originals/           # raw inputs the researcher drops in
│   │   ├── trimmed/             # 2.5-s clips at original resolution (intermediate, gitignored)
│   │   └── processed/           # final 480p / 24 fps versions
│   │       ├── practice/
│   │       └── qualification/
│   └── manifest_public.json     # generated (4 arrays), committed
├── secrets/                     # gitignored
│   ├── hash_map.tsv             # hashed_filename ↔ original_path (the link, NEVER served from the host)
│   └── manifest_private.json    # generated, NOT committed
├── experiment/
│   ├── index.html
│   ├── esbuild.config.mjs
│   ├── package.json
│   ├── public/
│   │   ├── stimuli.json         # copy of manifest_public.json served as a static asset
│   │   ├── styles.css
│   │   └── _videos              # symlink → ../../pipeline/staging_hashed/ (local dev only)
│   ├── src/
│   │   ├── main.js              # timeline construction
│   │   ├── config.js            # KEYS, TIMING, STRUCTURE, CONFIDENCE_LABELS, COMPLETION_CODES
│   │   ├── prolific.js          # URL-var capture + endSession()
│   │   ├── data.js              # DataPipe wrapper + localStorage fallback + export button
│   │   ├── stimuli.js           # loads stimuli.json, builds per-block lists (5 catch + 95 real / block)
│   │   └── trials/              # consent, instructions, familiarization, practice, qualification,
│   │                            #   main_blocks, block_end, debrief, trial_factory
│   └── dist/                    # build output (gitignored)
└── analysis/
    ├── load_data.py             # JSON-bundle / CSV loader, confidence-row merge, private-manifest join
    ├── explore.ipynb            # exploration notebook (per-session basics: RT, confidence, calibration, catch)
    ├── requirements.txt         # pandas, numpy, matplotlib, seaborn, jupyter
    ├── score.py                 # (planned) batch scoring across participants
    └── data/                    # exported session JSONs (gitignored)
```

## 5. Workflow

### Stage 1 (one-time per source-corpus update)
1. **Main corpus**: source videos in `videos/rescaled_final/` (`NNNN_fw.mp4` / `NNNN_rv.mp4`, gitignored).
   - Pilot first: `python pipeline/transcode.py --limit 50` → check 2–3 transcoded files for quality, tune `--crf` / `--short-side` if needed.
   - Full run: `python pipeline/transcode.py` → paper-trail copies in `pipeline/staging/`, hashed copies in `pipeline/staging_hashed/`, entries in `secrets/hash_map.tsv`.
2. **Catch trials**: `python pipeline/generate_catch_trials.py` → drops 10 instruction-text MP4s into `videos/rescaled_final/` (named `catch_<dir>_<C>.mp4`). Then re-run `transcode.py` to pick them up (incremental — fast).
3. **Obvious clips for practice + qualification**: drop the source videos into `pipeline/source_obvious/originals/` (any resolution / fps / container). Run `python pipeline/process_obvious.py` → trims to 2.5 s, encodes forward + reverse at 480p/24fps, hashes them, and appends entries to `secrets/hash_map.tsv` with `practice/` or `qualification/` paths. Auto-splits between practice and qualification by alphabetical halves; override by pre-organising into `originals/practice/` + `originals/qualification/` subdirs.
4. **Manifest**: `python pipeline/dev_link.py` (for local dev — symlinks videos into `experiment/public/_videos/` and runs `build_manifest.py` with `--base-url _videos`). For production, run `python pipeline/build_manifest.py --base-url <CDN URL>` directly. Both produce `pipeline/manifest_public.json` (committed) and `secrets/manifest_private.json` (gitignored).
5. **Upload**: `python pipeline/upload.py` → pushes `staging_hashed/` to the configured host. (Pending §2.5 / hosting decision.)
6. **Verify**: `python pipeline/verify.py` → fetches every URL, asserts 200 + correct MIME + CORS headers.
7. **Commit** `pipeline/manifest_public.json` (only — never `secrets/hash_map.tsv` or `secrets/manifest_private.json`).

### Stage 2 (per-iteration during development)
1. `cd experiment && npm install`.
2. `npm run dev` → local server with hot reload (esbuild watch + a tiny static server).
3. Test in Chromium, Firefox, Safari. **Run through the full task** — not just the first few trials — and watch for preload stutters, broken videos, or layout regressions on different viewports.
4. `npm run build` → outputs `experiment/dist/`.
5. Deploy to GitHub Pages (or Cloudflare Pages).
6. Configure the Prolific study: study URL with `?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}`, completion code matching the redirect.
7. Run a small internal pilot (3–5 participants) before launching at scale.

## 6. Open questions for the user

Resolved so far:
- ✅ Bot-resistance posture: no main-task labels on client; practice/qualification labels OK; catch trials interspersed in main blocks for attention/bot probing (§2.7, §3.9).
- ✅ Session length: 4 main blocks, **100 trials each**, ~45 min typical wall time, 1-hour soft cap as runaway-session safety net.
- ✅ Per-trial timing: 2.5-s video + 1-s direction response + participant-paced confidence response (no timeout), spacebar starts every trial.
- ✅ Source-clip length: uniform 2.5 s / 60 frames @ 24 fps.
- ✅ Block composition: 95 real + 5 catch trials per block; real trials counterbalanced 47/48 forward/backward within each block, exact 50/50 over the full session (§3.5).
- ✅ Response modality: keyboard. Direction: **← = backward**, **→ = forward**. Confidence: number keys **1–5**.
- ✅ Confidence scale labels: *guess · somewhat unsure · unsure · somewhat sure · certain*.
- ✅ Familiarization format: HTML text trials via `plugin-html-keyboard-response` (Layer A in §3.4), now ~8 trials covering direction + confidence + combined.
- ✅ Catch trials: 10 unique videos (2 directions × 5 confidence levels), one variant each. White text on dark grey, 480p, 2.5 s. **2** catch trials in Layer B practice; **1** in Layer C qualification (excluded from the gate fraction); 5 per main block.
- ✅ Catch-trial inclusion threshold: ≥ 80% **catch-direction** pass rate to be included in analysis (the direction key only — catch full-pass conflated honest confidence-key slips with genuine inattention; see §3.9).
- ✅ Catch-trial bonus: payment bonus contingent on ≥ 80% **catch-full** pass rate (direction AND confidence), **communicated to participants upfront** in instructions/consent. Paid manually after offline scoring. Inclusion and bonus are separate decisions.
- ✅ Counterbalancing target *N* = **20 participants per clip per direction** for the full deployment (4,400 unique clip-direction combinations × 20 = 88,000 viewings ÷ ~380 real trials/participant ≈ **~232 participants minimum**; budget ~260–280 to absorb dropouts/exclusions).
- ✅ Payment structure: per the §6 recommendation — ~£1.50 base + ~£2 × 4 main blocks + ~£1 attention bonus ≈ £10.50 max for ~45 min ≈ £14/hr.
- ✅ Qualification threshold: ≥ 75% direction accuracy (≥ 7/10 of qualification trials).
- ✅ **Practice + qualification clip set**: 20 externally-sourced clips (NOT in the main corpus) processed via `pipeline/process_obvious.py` (§2.8). Auto-split into 10 practice + 10 qualification source clips → 20 + 20 files (fw + rv). Pool isolation guarantees no leak between practice and main blocks. The earlier "70 = 2×35 selection from main" plan is **superseded** by this — the selection model leaks because selected clips were also in main; the new external-source model does not.
- ✅ **Audio in sources**: confirmed dropped (`-an` everywhere). Reversed audio is unnatural and audio carries no AoT signal.
- ✅ **Source-corpus metadata / stratification**: not stratifying by category (action type, scene). The corpus has no per-clip metadata available, and at 380 real trials / participant × 232 participants the 4358 main clips are seen often enough to spread variability without explicit stratification.
- ✅ **Consent text and ethics**: institutional consent text in place (Shufan Zhang, Ningkai Wang, Tomas Knapen) and implemented as the first user-visible screen (§3.3 phase 2). Decline → `endSession('consentDeclined')` with a Prolific completion code that pays for time spent.

Still open:

1. **Hosting** — pending university IT contact. Once you know what's available (uni-provided object storage, Cloudflare R2, AWS, etc.), come back and we'll lock §2.5 / §3.2.
2. **DataPipe + OSF accounts** — pending; both free, can be set up by you in ~30 min. Required before deployment.
3. **Browser/device check screen** — `@jspsych/plugin-browser-check` is in `package.json` but not yet wired into the timeline. To add: minimum viewport 1024×600, desktop only, modern Chromium/Firefox/Safari (§3.3 phase 4).
4. **Real Prolific completion codes** — for the JATOS deployment path (which is what we're using), the completion code is configured **in the JATOS study settings**, not in this bundle. `experiment/src/config.js` `COMPLETION_CODES` only matters if you ever deploy without JATOS. Keeping codes server-side in JATOS prevents extraction from the client and lets you rotate them without a redeploy.

## 7. Things explicitly out of scope (for v1)

- Mobile/tablet support.
- Multi-codec fallbacks (HEVC/AV1).
- Real-time eye tracking, mouse tracking, or webcam recording.
- Adaptive trial selection (e.g., staircase). Trials are pre-sampled per participant.
- Live accuracy feedback during main blocks (precluded by the bot-resistance posture; §3.7).
- A custom backend / serverless ground-truth oracle (kept as the §3.9 fallback).
- Multi-session designs (return-visit Prolific flows) — single session only.

## 8. Style and conventions for code in this repo

- Python: Python 3.11+, type-hinted, `ruff` + `black` defaults, no framework — plain stdlib + boto3 + python-dotenv.
- JavaScript: ES modules, no TypeScript for v1 (keep the bundle simple), no React. jsPsych's own API is the abstraction layer.
- No secrets in source. `.env` only. `manifest_private.json` is gitignored.
- Comments explain *why*, not *what*. Particularly important to comment any place where bot-resistance assumptions are load-bearing — if a future change leaks ground truth to the client, the comment should make that obvious.

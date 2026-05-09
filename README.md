# Arrow-of-Time online 2-AFC experiment

Online psychophysics experiment in which Prolific participants view short
video clips and judge whether each clip plays **forward** or **backward**,
followed by a 1–5 confidence rating. Built on jsPsych v8, served as a
static bundle, with a Python + ffmpeg stimulus pipeline.

For the **why** (design decisions, bot resistance, payment structure,
trial counts, etc.) see [`CLAUDE.md`](CLAUDE.md). This README covers
**how** — installation, running, and validation.

---

## Prerequisites

- **Python ≥ 3.11** (the analysis side uses pandas 2 + numpy 2; we
  develop in a conda env named `neuro` but anything that satisfies
  `pipeline/requirements.txt` and `analysis/requirements.txt` works)
- **ffmpeg** with libx264 (`brew install ffmpeg` on macOS)
- **Node ≥ 20** + npm (Node 22+ tested)
- ~5 GB of disk for the source corpus, transcoded copies, and hashed
  deployment copies (gitignored)

The repository on its own is small (~25k lines of code + a 380 KB
public manifest). The bulk comes from the source video corpus that
lives outside the repo (see [Stage 1](#stage-1--stimulus-pipeline)).

## Quickstart

Once the videos are on disk:

```bash
# 0. clone + cd
git clone <this-repo> && cd online_experiment

# 1. pipeline (Python)
python -m pip install -r pipeline/requirements.txt
python pipeline/transcode.py                 # main corpus → staging + hashed
python pipeline/generate_catch_trials.py     # 10 instruction-text MP4s
python pipeline/transcode.py                 # incremental: pick up catches
python pipeline/process_obvious.py           # external practice/qual clips
python pipeline/dev_link.py                  # symlink + manifest for local dev

# 2. experiment (Node)
cd experiment
npm install
npm run dev                                  # → http://localhost:3000/

# 3. analysis (Python)
python -m pip install -r analysis/requirements.txt
jupyter lab analysis/explore.ipynb
```

The full flow including validation checks is below.

---

## Stage 1 — stimulus pipeline

The pipeline produces hashed-filename MP4s in `pipeline/staging_hashed/`
plus two manifests (`pipeline/manifest_public.json` and
`secrets/manifest_private.json`). Run the steps in order; each is
idempotent — re-running picks up only new files.

### 1. Place the source corpus

The main corpus is **NOT** in the repo (it's gitignored). Drop the
`NNNN_fw.mp4` / `NNNN_rv.mp4` pairs into `videos/rescaled_final/`:

```
videos/rescaled_final/
├── 0001_fw.mp4
├── 0001_rv.mp4
├── 0002_fw.mp4
└── ...
```

Source clips must be **2.5 s, 60 frames at 24 fps** (uniform across the
corpus — the main-corpus transcoder doesn't trim or pad).

### 2. Transcode the main corpus

```bash
python pipeline/transcode.py                  # full run
python pipeline/transcode.py --limit 50       # pilot (random sample, seed=42)
```

Outputs:
- `pipeline/staging/`         — paper-trail copies at the target encoding
- `pipeline/staging_hashed/`  — same files with random-hex names
- `secrets/hash_map.tsv`      — `hashed_filename ↔ original_path`

Encoding: H.264 / 480p short side / CRF 28 / `-preset slow` /
`-pix_fmt yuv420p` / `+faststart` / audio dropped. Override via
`--crf`, `--short-side`, `--preset`.

### 3. Generate catch trials

10 instruction-text MP4s ("Press FORWARD / Then press 3" etc.) shown
as on-screen attention checks during main blocks (CLAUDE.md §2.7):

```bash
python pipeline/generate_catch_trials.py            # full set
python pipeline/generate_catch_trials.py \
       --preview-only catch_fwd_3                   # eyeball one
```

These land in `videos/rescaled_final/` alongside real clips. Re-run
`transcode.py` afterwards (incremental — fast) so they pick up hashes.

### 4. Process the obvious clips

Practice + qualification clips come from a **separate pool** that
never overlaps with the main corpus (so practice can't leak into the
main task). Drop raw video files (any resolution / fps / container)
into `pipeline/source_obvious/originals/` and run:

```bash
python pipeline/process_obvious.py
```

This trims each input to 2.5 s (default: center of the clip; override
with `--start-sec X` or a per-file `originals/start_times.json`),
produces forward + reverse renders at 480p / 24 fps / CRF 28, hashes
them, and appends entries to `secrets/hash_map.tsv` under
`practice/<stem>_fw.mp4` etc.

By default it splits clips alphabetically half-and-half between
practice and qualification. Override by pre-organising into
`originals/practice/` + `originals/qualification/` subdirs.

### 5. Wire up local dev

```bash
python pipeline/dev_link.py
```

This:
- writes `pipeline/manifest_public.json` (committed) and
  `secrets/manifest_private.json` (gitignored)
- writes a local-dev copy `experiment/public/stimuli.json` with
  relative URLs
- creates a symlink `experiment/public/_videos → pipeline/staging_hashed`
  so the dev server resolves video URLs

For a production manifest with absolute URLs (after the hosting
decision is made), run `build_manifest.py` directly:

```bash
python pipeline/build_manifest.py --base-url https://your-host.example/path
```

## Stage 2 — running the experiment

```bash
cd experiment
npm install                  # one-time
npm run dev                  # → http://localhost:3000/
```

Open `http://localhost:3000/?PROLIFIC_PID=test123`. You'll walk through:

```
consent → welcome → Layer A familiarization (8 HTML trials)
       → task intro (with bonus disclosure)
       → Layer B practice (12 real clips with feedback + 1 catch demo)
       → Layer C qualification (10 obvious clips, ≥75% direction gate)
       → main blocks: 4 × 100 trials (95 real + 5 catch interleaved)
       → debrief (age + gender + free-text)
       → save → end-of-session page
```

### Quick-iteration mode

The full flow takes ~45 minutes. To exercise the timeline end-to-end
in ~2 minutes, temporarily edit `experiment/src/config.js`:

```javascript
export const STRUCTURE = {
  trialsPerMainBlock: 5,        // was 100
  realTrialsPerMainBlock: 4,    // was 95
  catchTrialsPerMainBlock: 1,   // was 5
  ...
};
```

Reload — `npm run dev` watches and rebuilds automatically. Revert
before any real participant runs.

### Production build

```bash
npm run build       # → experiment/dist/ (static files)
```

Deploy `dist/` to GitHub Pages, Netlify, Cloudflare Pages, or any
static host. Configure the Prolific study URL as
`https://your-host/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}`.

Before deployment, fill in the placeholder values in
`experiment/src/config.js`:

- `DATAPIPE_EXPERIMENT_ID` — from your DataPipe project dashboard
- `COMPLETION_CODES.{finished,familiarizationFailed,qualificationFailed,consentDeclined}`
  — from your Prolific study dashboard

The placeholders fail loudly (Prolific rejects `'PLACEHOLDER_*'`
codes; without DataPipe configured, data falls back to `localStorage`
silently). Both behaviours are intentional so a forgotten edit is
obvious before a participant ever runs the study.

---

## Validation

Run these checks after each pipeline + experiment cycle.

### Pipeline integrity

```bash
# every hashed file referenced by the TSV exists, and vice versa
python - <<'PY'
import csv
from pathlib import Path
referenced = set()
with open('secrets/hash_map.tsv') as f:
    for row in csv.DictReader(f, delimiter='\t'):
        referenced.add(row['hashed_filename'])
present = {p.name for p in Path('pipeline/staging_hashed').glob('*.mp4')}
print(f"  referenced: {len(referenced)}, present: {len(present)}")
print(f"  orphans: {len(present - referenced)}, missing: {len(referenced - present)}")
PY
```

Expect: orphans = 0, missing = 0.

### Manifest counts

```bash
python - <<'PY'
import json
m = json.load(open('experiment/public/stimuli.json'))
print(f"  main:          {len(m['main']):>5d}")
print(f"  catch:         {len(m['catch']):>5d}    (expect 10)")
print(f"  practice:      {len(m['practice']):>5d}    (≥ 12)")
print(f"  qualification: {len(m['qualification']):>5d}    (≥ 10)")
PY
```

### Bot-resistance invariant

The public manifest's `main` and `catch` arrays must NOT carry
direction labels:

```bash
python - <<'PY'
import json
m = json.load(open('experiment/public/stimuli.json'))
forbidden = {'direction', 'expected_confidence', 'is_catch', 'type', 'source_file'}
for arr in ('main', 'catch'):
    bad = [e['stimulus_id'] for e in m[arr] if forbidden & set(e.keys())]
    print(f"  {arr}: {'LEAK' if bad else 'clean'}", bad[:3] or '')
PY
```

Expect: both arrays clean. If anything is flagged, **do not deploy**
— it means a code change has accidentally exposed ground truth.

### Dev-server liveness

```bash
cd experiment
npm run dev > /tmp/aot_dev.log 2>&1 &
DEV_PID=$!
sleep 2

# bundle parses
curl -s http://localhost:3000/experiment.bundle.js | node --check -

# a video is reachable through the symlink with range support
URL=$(python -c "import json; print(json.load(open('public/stimuli.json'))['main'][0]['url'])")
curl -s -I -r 0-1023 "http://localhost:3000/$URL" | head -5

kill $DEV_PID
```

Expect: `node --check` succeeds, the range request returns
`HTTP/1.1 206 Partial Content` with `Content-Range: bytes 0-1023/...`.

### End-to-end walkthrough

Open `http://localhost:3000/?PROLIFIC_PID=test123`. Quick-iteration
mode (above) makes this fast. Verify:

- consent screen shows; **I do not agree** terminates cleanly with a
  debug page
- 8 familiarization trials advance correctly (←/→ for direction, 1–5
  for confidence)
- practice block shows feedback ("Correct" / "Not quite") after each
  real-clip trial; the catch demo plays without feedback
- qualification block has no feedback; failing it ends the session
- main blocks show no feedback at all; catch trials appear
  occasionally (~5/100, distinguishable visually)
- between blocks there's a single **Continue** button; mid-session
  break has a 30-second countdown
- final save runs, then the local-dev end page shows captured PID
  and a **Download saved data** button

---

## Analysis

```bash
python -m pip install -r analysis/requirements.txt
jupyter lab analysis/explore.ipynb
```

Workflow:

1. Walk through the experiment locally (any session counts in dev
   mode — the data is saved to `localStorage`).
2. On the local-dev end page, click **Download saved data** to
   download a JSON bundle of every saved session.
3. Drop the file into `analysis/data/` (gitignored).
4. Open `analysis/explore.ipynb`. The first cell auto-picks the most
   recent file. The second cell lists every session in the bundle —
   set `SESSION_PID` to override the auto-pick.
5. Run all. The notebook covers row counts by phase, Layer A
   summary, main-task RT distribution, confidence histogram,
   forward-response bias, per-block summary, catch-trial pass rate
   (offline join with the private manifest), confidence calibration.

The loader (`analysis/load_data.py`) handles the JSON-bundle ↔ CSV
distinction, merges confidence values onto canonical stimulus rows,
and joins per-trial data with the private manifest to reconstruct
main-trial correctness post-hoc.

---

## What's tracked vs. ignored

Tracked in this repo:
- All source code (Python pipeline, jsPsych experiment, analysis loader + notebook)
- Config and lockfile (`package-lock.json`, `requirements.txt`)
- The **public** stimulus manifest (`pipeline/manifest_public.json`)
- `CLAUDE.md` (design doc) and this README

Gitignored (lives only on the analyst's / researcher's machine):
- Source video corpus (`videos/`)
- Pipeline outputs (`pipeline/staging/`, `pipeline/staging_hashed/`,
  `pipeline/source_obvious/{originals,trimmed,processed}/`)
- Secrets (`secrets/hash_map.tsv`, `secrets/manifest_private.json`)
- Per-participant data exports (`analysis/data/`)
- Build artifacts (`experiment/dist/`, `experiment/node_modules/`)
- Local-dev artifacts (`experiment/public/_videos`,
  `experiment/public/stimuli.json`)

A fresh clone won't run the experiment without (a) the video corpus
and (b) running the pipeline — by design, since neither belongs in
git.

---

## Where to learn more

- [`CLAUDE.md`](CLAUDE.md) — single source of truth for design
  decisions, the experimental protocol, the manifest schema, the
  bot-resistance posture, and the open questions
- jsPsych v8 docs — <https://www.jspsych.org/v8/>
- DataPipe — <https://github.com/jspsych/datapipe>
- The Meding et al. arrow-of-time paper that the protocol is
  modelled after — PMC10113813

#!/usr/bin/env python3
"""
Build manifest_public.json (committed) and manifest_private.json (gitignored)
from the hash_map.tsv produced by transcode.py.

Each TSV entry's `original_path` is parsed to derive metadata. The recognised
naming conventions (CLAUDE.md §2.7):

  NNNN_fw.mp4              → main, real, direction = forward
  NNNN_rv.mp4              → main, real, direction = backward
  catch_fwd_C.mp4          → main, catch, direction = forward, expected_confidence = C
  catch_rv_C.mp4           → main, catch, direction = backward
  practice/<name>_fw.mp4   → practice, real, direction = forward
  practice/<name>_rv.mp4   → practice, real, direction = backward
  qualification/<name>_fw.mp4 → qualification, real, direction = forward
  qualification/<name>_rv.mp4 → qualification, real, direction = backward

The two manifests differ structurally:

  manifest_public.json (shipped with experiment, committed):
    {
      "main":          [{"stimulus_id":"...", "url":"..."}, ...],   ← real + catch interleaved,
                                                                       no direction or is_catch flag
      "practice":      [{"stimulus_id":"...", "url":"...", "direction":"forward"}, ...],
      "qualification": [...]
    }

  manifest_private.json (gitignored, used by analysis/score.py only):
    [
      {"stimulus_id":"...", "source_file":"...", "type":"main", "direction":"..."},
      {"stimulus_id":"...", "source_file":"catch_fwd_3.mp4", "type":"catch",
       "direction":"forward", "expected_confidence":3},
      {"stimulus_id":"...", "source_file":"practice/burning_paper_fw.mp4",
       "type":"practice", "direction":"forward"},
      ...
    ]

Bot-resistance constraint (CLAUDE.md §3.9): the public manifest's `main`
array MUST NOT carry direction labels or any flag that distinguishes catch
trials from real clips. Both kinds appear there as plain {stimulus_id, url}.

Usage (run from repo root):

    python pipeline/build_manifest.py
    python pipeline/build_manifest.py --base-url https://media.example.com/aot
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

# Recognised filename patterns. Anchored on the basename only (parent dir
# determines phase for practice/qualification).
RE_REAL_MAIN = re.compile(r"^(\d+)_(fw|rv)\.mp4$")
RE_CATCH = re.compile(r"^catch_(fwd|rv)_([1-5])\.mp4$")
RE_OBVIOUS = re.compile(r"^(.+)_(fw|rv)\.mp4$")  # any-stem, _fw or _rv suffix


def parse_metadata(rel_path_str: str) -> dict:
    """Parse one entry's original_path → metadata dict.

    Returns one of:
      {'phase':'main',           'is_catch': False, 'direction': ...}
      {'phase':'main',           'is_catch': True,  'direction': ..., 'expected_confidence': int}
      {'phase':'practice',       'is_catch': False, 'direction': ...}
      {'phase':'qualification',  'is_catch': False, 'direction': ...}

    Raises ValueError on filenames that don't match any recognised pattern.
    """
    rel = Path(rel_path_str.replace("\\", "/"))
    parts = rel.parts
    name = rel.name

    # Phase derived from parent directory.
    phase = "main"
    if parts and parts[0] == "practice":
        phase = "practice"
    elif parts and parts[0] == "qualification":
        phase = "qualification"

    # Catch trials only valid in main.
    if phase == "main":
        m = RE_CATCH.match(name)
        if m:
            return {
                "phase": "main",
                "is_catch": True,
                "direction": "forward" if m.group(1) == "fwd" else "backward",
                "expected_confidence": int(m.group(2)),
            }

        m = RE_REAL_MAIN.match(name)
        if m:
            return {
                "phase": "main",
                "is_catch": False,
                "direction": "forward" if m.group(2) == "fw" else "backward",
            }

    if phase in ("practice", "qualification"):
        m = RE_OBVIOUS.match(name)
        if m:
            return {
                "phase": phase,
                "is_catch": False,
                "direction": "forward" if m.group(2) == "fw" else "backward",
            }

    raise ValueError(f"can't parse: {rel_path_str}")


def _resolve_source_obvious(
    *,
    source_obvious_dir: Path,
    public: dict,
    private: list[dict],
    seed: int,
) -> set[str]:
    """Reclassify main entries → practice / qualification based on the
    contents of source_obvious_dir.

    The directory may be organised in two ways:

      A. With explicit subdirs: source_obvious/practice/* and
         source_obvious/qualification/*. Each file's basename must match
         an entry already in the main pool.

      B. Flat (no subdirs): all files at source_obvious/*.mp4. We then
         auto-split the unique source-clip IDs (each `<id>` has both
         `_fw` and `_rv`) half-and-half between practice and qualification,
         using a seeded shuffle for reproducibility. Practice gets the
         remainder when the count is odd.

    Returns the set of stimulus_ids that were reclassified. The matching
    entries are removed from public['main'] and added to public['practice']
    / public['qualification'] (with their direction label).
    """
    if not source_obvious_dir.exists():
        return set()

    # Discover files. Subdirs win; if none we treat as flat.
    practice_names: list[str] = []
    qualification_names: list[str] = []
    flat_names: list[str] = []
    for p in source_obvious_dir.rglob("*.mp4"):
        if not p.is_file():
            continue
        rel = p.relative_to(source_obvious_dir)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "practice":
            practice_names.append(p.name)
        elif len(parts) >= 2 and parts[0] == "qualification":
            qualification_names.append(p.name)
        elif len(parts) == 1:
            flat_names.append(p.name)

    if not practice_names and not qualification_names and not flat_names:
        return set()

    # Auto-split the flat list if no subdirs were used.
    if flat_names and not practice_names and not qualification_names:
        # Each source clip has both fw and rv. Split by source ID so a
        # given clip lands wholly in practice or wholly in qualification.
        source_ids = sorted({
            re.sub(r"_(fw|rv)\.mp4$", "", n) for n in flat_names
        })
        rng = random.Random(seed)
        rng.shuffle(source_ids)
        n_practice_sources = (len(source_ids) + 1) // 2  # practice gets the extra on odd
        prac_set = set(source_ids[:n_practice_sources])
        qual_set = set(source_ids[n_practice_sources:])
        flat_lookup = set(flat_names)
        for sid in prac_set:
            for sfx in ("fw", "rv"):
                fname = f"{sid}_{sfx}.mp4"
                if fname in flat_lookup:
                    practice_names.append(fname)
        for sid in qual_set:
            for sfx in ("fw", "rv"):
                fname = f"{sid}_{sfx}.mp4"
                if fname in flat_lookup:
                    qualification_names.append(fname)

    # Build a name -> private entry lookup for quick stimulus_id resolution.
    # Note: the private manifest's `source_file` may include a leading
    # subdirectory in some convention, but for the typical flat main
    # corpus they're just basenames.
    name_to_private = {
        Path(e["source_file"]).name: e
        for e in private
        if e.get("type") == "main"
    }

    moved_ids: set[str] = set()

    def reclassify(filenames: list[str], target: str) -> None:
        for fname in filenames:
            entry = name_to_private.get(fname)
            if entry is None:
                print(
                    f"warning: source_obvious file '{fname}' not found in main pool — skipped.",
                    file=sys.stderr,
                )
                continue
            sid = entry["stimulus_id"]
            idx = next(
                (i for i, e in enumerate(public["main"]) if e["stimulus_id"] == sid),
                None,
            )
            if idx is None:
                # already moved (e.g. listed twice) — don't double-add
                continue
            moved = public["main"].pop(idx)
            public[target].append({**moved, "direction": entry["direction"]})
            moved_ids.add(sid)

    reclassify(practice_names, "practice")
    reclassify(qualification_names, "qualification")
    return moved_ids


def _bootstrap_practice_from_main(
    *,
    public: dict,
    private: list[dict],
    n_practice: int,
    n_qualification: int,
    seed: int,
) -> set[str]:
    """Pull a balanced set of clips from public['main'] and use them as
    practice + qualification entries (with direction labels), removing
    them from public['main'] so a participant doesn't see them twice.

    Returns the set of stimulus_ids that were bootstrapped.

    Direction is sourced from the private manifest. The split is balanced
    forward/backward as best as possible; with the typical N=12 + N=10
    we end up with 6+6 practice and 5+5 qualification.
    """
    # Build a stimulus_id -> direction map from private (real main only).
    sid_to_dir: dict[str, str] = {
        e["stimulus_id"]: e["direction"]
        for e in private
        if e.get("type") == "main"
    }

    by_dir: dict[str, list[dict]] = {"forward": [], "backward": []}
    for entry in public["main"]:
        d = sid_to_dir.get(entry["stimulus_id"])
        if d in by_dir:
            by_dir[d].append(entry)

    rng = random.Random(seed)
    rng.shuffle(by_dir["forward"])
    rng.shuffle(by_dir["backward"])

    def take(pool: str, n: int) -> list[dict]:
        chunk, rest = by_dir[pool][:n], by_dir[pool][n:]
        by_dir[pool] = rest
        return chunk

    # Practice: half forward, half backward (round up to forward on odd N)
    pf = (n_practice + 1) // 2
    pb = n_practice - pf
    qf = (n_qualification + 1) // 2
    qb = n_qualification - qf

    pract_fw = take("forward", pf)
    pract_rv = take("backward", pb)
    qual_fw = take("forward", qf)
    qual_rv = take("backward", qb)

    chosen_practice = pract_fw + pract_rv
    chosen_qualification = qual_fw + qual_rv

    # Add to public.practice / public.qualification with direction.
    for e in chosen_practice:
        public["practice"].append({**e, "direction": sid_to_dir[e["stimulus_id"]]})
    for e in chosen_qualification:
        public["qualification"].append({**e, "direction": sid_to_dir[e["stimulus_id"]]})

    # Remove them from public.main.
    chosen_ids = {e["stimulus_id"] for e in chosen_practice + chosen_qualification}
    public["main"] = [e for e in public["main"] if e["stimulus_id"] not in chosen_ids]
    return chosen_ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tsv", type=Path, default=Path("secrets/hash_map.tsv"),
        help="Hash-map TSV produced by transcode.py. (default: %(default)s)",
    )
    parser.add_argument(
        "--public-out", type=Path, default=Path("pipeline/manifest_public.json"),
        help="Where to write the public manifest. (default: %(default)s)",
    )
    parser.add_argument(
        "--private-out", type=Path, default=Path("secrets/manifest_private.json"),
        help="Where to write the private manifest. (default: %(default)s)",
    )
    parser.add_argument(
        "--base-url", default="",
        help="URL prefix prepended to each hashed filename. "
             "Empty (default) → bare filenames; rewrite later when hosting is locked.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero if any TSV entry fails to parse "
             "(default: skip unparsable rows, warn, exit 0).",
    )
    parser.add_argument(
        "--source-obvious-dir", type=Path, default=Path("pipeline/source_obvious"),
        help="Directory of selected obvious clips (flat, or with practice/ "
             "+ qualification/ subdirs). Each filename here must match an "
             "entry already in the main TSV — these are SELECTIONS, not "
             "new sources. (default: %(default)s)",
    )
    parser.add_argument(
        "--no-auto-practice", action="store_true",
        help="Don't auto-bootstrap practice/qualification from the main pool "
             "when no source_obvious clips are found. Default: fall back to "
             "the bootstrap (random selection from main).",
    )
    parser.add_argument(
        "--bootstrap-practice", type=int, default=12,
        help="When auto-bootstrapping, number of practice trials to pull "
             "from main. (default: %(default)s)",
    )
    parser.add_argument(
        "--bootstrap-qualification", type=int, default=10,
        help="When auto-bootstrapping, number of qualification trials to "
             "pull from main. (default: %(default)s)",
    )
    parser.add_argument(
        "--bootstrap-seed", type=int, default=0,
        help="Random seed for the auto-bootstrap selection. Same seed -> "
             "same practice + qualification clips. (default: %(default)s)",
    )
    args = parser.parse_args()

    if not args.tsv.exists():
        print(f"error: tsv not found at {args.tsv}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    with args.tsv.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append(row)

    if not rows:
        print(f"error: tsv at {args.tsv} is empty", file=sys.stderr)
        return 2

    public = {"main": [], "catch": [], "practice": [], "qualification": []}
    private: list[dict] = []
    parse_errors: list[str] = []

    base = args.base_url.rstrip("/")

    for row in rows:
        hashed = row["hashed_filename"]
        original = row["original_path"]
        try:
            meta = parse_metadata(original)
        except ValueError as e:
            parse_errors.append(str(e))
            continue

        stimulus_id = Path(hashed).stem
        url = f"{base}/{hashed}" if base else hashed

        # ---- private manifest: full metadata ----
        priv = {
            "stimulus_id": stimulus_id,
            "source_file": original,
            "direction": meta["direction"],
        }
        if meta["is_catch"]:
            priv["type"] = "catch"
            priv["expected_confidence"] = meta["expected_confidence"]
        else:
            priv["type"] = meta["phase"]  # 'main' | 'practice' | 'qualification'
        private.append(priv)

        # ---- public manifest ----
        # Real and catch entries are kept in SEPARATE arrays so the runtime
        # can deliberately mix N_catch + N_real per block. Within each
        # array the dicts are still just {stimulus_id, url} — the
        # direction label is never exposed for either real-main or catch.
        # A bot reading the bundle now knows which ~10 stimuli are catch
        # trials (they're in the `catch` array) but must still decode the
        # video content to comply with each one — and the 4,000+ real
        # stimuli remain unidentifiable. CLAUDE.md §3.9 covers the
        # bot-resistance reasoning.
        if meta["phase"] == "main":
            target = "catch" if meta["is_catch"] else "main"
            public[target].append({"stimulus_id": stimulus_id, "url": url})
        else:
            public[meta["phase"]].append(
                {"stimulus_id": stimulus_id, "url": url, "direction": meta["direction"]}
            )

    # ---- pick practice + qualification clips ----
    #
    # Three paths, in priority order:
    #
    #   1. **source_obvious selection** — the researcher has dropped
    #      `<id>_fw.mp4` / `<id>_rv.mp4` files (or organised into
    #      practice/ + qualification/ subdirs) under
    #      pipeline/source_obvious/. Those filenames must match entries
    #      already in the main hash_map.tsv: they're SELECTIONS, not new
    #      sources. We move the matching entries from public['main'] into
    #      public['practice']/['qualification'] using the existing hashes
    #      (no re-transcoding needed). This is the preferred, intentional
    #      path.
    #
    #   2. **TSV-derived** — files whose original_path begins with
    #      practice/ or qualification/ in the TSV (legacy convention,
    #      preserved for back-compat). Those are populated upstream
    #      during the parse loop above; if any are present we don't
    #      bootstrap further.
    #
    #   3. **Random bootstrap** — last resort. Pull a small balanced set
    #      of clips from main and use them as practice + qualification.
    #      Used only if neither of the above produced any entries.
    moved_from_obvious: set[str] = set()
    bootstrapped_ids: set[str] = set()

    if not public["practice"] and not public["qualification"]:
        moved_from_obvious = _resolve_source_obvious(
            source_obvious_dir=args.source_obvious_dir,
            public=public,
            private=private,
            seed=args.bootstrap_seed,
        )

    if (
        not args.no_auto_practice
        and not public["practice"]
        and not public["qualification"]
        and (args.bootstrap_practice or args.bootstrap_qualification)
    ):
        bootstrapped_ids = _bootstrap_practice_from_main(
            public=public,
            private=private,
            n_practice=args.bootstrap_practice,
            n_qualification=args.bootstrap_qualification,
            seed=args.bootstrap_seed,
        )

    # Stable order for clean diffs.
    for arr in public.values():
        arr.sort(key=lambda x: x["stimulus_id"])
    private.sort(key=lambda x: x["stimulus_id"])

    # Write atomically-enough: write to a tmp path, then rename.
    args.public_out.parent.mkdir(parents=True, exist_ok=True)
    args.private_out.parent.mkdir(parents=True, exist_ok=True)
    with args.public_out.open("w") as f:
        json.dump(public, f, indent=2)
        f.write("\n")
    with args.private_out.open("w") as f:
        json.dump(private, f, indent=2)
        f.write("\n")

    # Report
    n_main_real = sum(1 for e in private if e["type"] == "main")
    n_main_catch = sum(1 for e in private if e["type"] == "catch")
    n_practice = sum(1 for e in private if e["type"] == "practice")
    n_qual = sum(1 for e in private if e["type"] == "qualification")

    print(f"manifests written:")
    print(f"  public  → {args.public_out}")
    print(f"    main:          {len(public['main'])} real entries  (no direction labels)")
    print(f"    catch:         {len(public['catch'])} catch entries  (no labels; pulled separately by the runtime)")
    print(f"    practice:      {len(public['practice'])} entries  (with direction)")
    print(f"    qualification: {len(public['qualification'])} entries  (with direction)")
    print(f"  private → {args.private_out}  ({len(private)} entries; KEEP GITIGNORED)")
    if moved_from_obvious:
        print()
        print(
            f"note: reclassified {len(moved_from_obvious)} clips from "
            f"`{args.source_obvious_dir}` as practice + qualification. "
            f"These were already in the main pool and have been removed "
            f"from public['main'] so the participant doesn't see them "
            f"twice in a session."
        )
    elif bootstrapped_ids:
        print()
        print(
            f"note: source_obvious clips not present — bootstrapped "
            f"{len(bootstrapped_ids)} entries from the main pool to fill "
            f"practice + qualification. These are excluded from the main "
            f"array (so a participant doesn't see them twice). Once you "
            f"drop hand-picked clips into pipeline/source_obvious/, the "
            f"bootstrap auto-disables and these clips return to main."
        )

    if parse_errors:
        print()
        print(
            f"warning: {len(parse_errors)} unparsable entries skipped "
            f"(see CLAUDE.md §2.7 for naming conventions):",
            file=sys.stderr,
        )
        for msg in parse_errors[:10]:
            print(f"  {msg}", file=sys.stderr)
        if len(parse_errors) > 10:
            print(f"  ... and {len(parse_errors) - 10} more", file=sys.stderr)
        if args.strict:
            return 1

    if not base:
        print()
        print("note: --base-url not given; URLs in the public manifest are bare filenames.")
        print(
            "      re-run with --base-url 'https://your-host.example.com/path' "
            "once hosting is locked.",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

---
name: assess-enrichment
description: Measure the quality of localfiles enrichment (MusicBrainz / AcoustID / Deezer / filesystem fallback) by scanning `localfiles.db`. Use when the user asks to "assess enrichment", "score the metadata quality", "find bad album / artist matches", "check enrichment coverage", "evaluate enrichment against filenames", or wants a before/after baseline to evaluate a change to the enricher. Six internal-signal metric families plus a filename-ground-truth section that, for flat `Artist - Title` libraries, compares enriched fields against the filename to surface diverged/bad matches (self-calibrates and suppresses itself when the convention doesn't hold). Emits `tmp/enrichment_assess/findings.json` (machine-diffable) and `tmp/enrichment_assess/REPORT.md` (human-readable).
---

# assess-enrichment

Reproducible quality baseline for the localfiles enricher. Designed to be re-run after any change to `enricher/*` and compared diff-wise — the JSON output is the source of truth; the Markdown is for humans.

The skill assumes:

- DB at `/home/envel/kalinka/localfiles.db` (override with `KALINKA_DB`)
- The DB has been through at least one enrichment pass (so `match_score`, `mbid`, etc. are populated for at least some rows)

It does **not** require any external API calls and runs in ~1 second.

## What it measures

Seven families of signals. The first six are internal-signal metrics (the DB judged against itself); the seventh (filename ground truth) adds an external reference for flat `Artist - Title` libraries. Each maps directly to a fix discussed in the enrichment review:

### 1. Coverage

How much of the library is enriched at all. Low coverage masks every other metric, so this is reported first.

| Metric | Good if |
|---|---|
| artists enriched (`enriched != 0`) | ≥95% |
| albums enriched | ≥95% |
| tracks enriched | ≥95% |
| artists with MBID | ≥60% |
| albums with MBID | ≥50% |
| albums with genre | ≥70% |
| albums with year | ≥70% |
| tracks with MBID | ≥50% |

These thresholds are intentionally tolerant — Jamendo / unreleased tracks legitimately won't have MBIDs.

### 2. Artist hygiene

Catches the issues from the review (leading punctuation, hyphen variants, etc.). All of these should be **zero** after the normalization fix lands.

- `leading_punct` — artist `name` starts with `[-_.,/ ]`
- `trailing_punct` — artist `name` ends with `[-_.,/ ]`
- `multi_space` — `name` contains `  ` (two or more spaces)
- `probable_duplicates` — groups of distinct artist rows whose **normalized** names collide. Normalization: lowercase → NFKD → strip combining marks → replace `[-_./]` with space → collapse whitespace. Each group reports the conflicting names and IDs so a merge migration can target them.

### 3. Album hygiene

The path-leak issues from the review.

- `path_artifact_titles` — album titles matching any of: `---`, `MP3` / `FLAC` / `WAV` / `WEB` / `WEBM` / `OGG` tokens, runs of ≥5 digits, leading-or-trailing punctuation, `Jamendo` / `Beatport`, `CD[0-9]+`. These are almost always folder-name leaks.
- `folder_splits` — parent directories on disk that host tracks belonging to **more than one** album_id. Each entry includes the path, the conflicting album titles, and the track count. This is the smoking gun for the "same folder split into multiple albums" issue.
- `va_candidates` — folders where ≥4 tracks share a parent dir AND ≥75% of tracks within have distinct artist_ids. Likely V/A compilations the indexer should have collapsed into one album.

### 4. Match-score distribution

Histogram of `match_score` and `match_similarity` per entity type. Two things to watch:

- A bimodal distribution with a tail below the threshold means low-confidence picks are sneaking in.
- A perfectly flat distribution at 100 means the score isn't being used as a discriminator (current state for artists/albums — worth investigating).

The report flags the **bottom-10 committed matches** for each entity type so the user can eyeball whether they look correct.

### 5. Cross-entity consistency

Detects "we matched the album but the tracks are orphans" and vice versa.

- `enriched_albums_with_unmatched_tracks` — albums where the album has an MBID but ≥1 track inside it has no MBID. Sometimes legitimate (rare recording, no MB entry) but usually a sign the track-matcher couldn't disambiguate. After cross-track consensus + duration fixes, this number should drop.
- `tracks_with_mbid_in_unmatched_albums` — the opposite: track is matched but its album isn't. Usually means the album title is a path artifact (see §3).
- `mistagging_candidates` — small albums (≤3 tracks) where the tracks span multiple artists. After the folder-bounded album-id change, the original "Abbey Road forks into three" case shows up here — mostly tag-quality issues to fix at the source.
- `va_albums_to_coalesce` — larger albums (≥4 tracks, ≥4 distinct artists). V/A compilations the indexer's detach pass will dissolve on the next index (tracks move to `unknown_album` and surface under their real artist via the orphan-tracks fallback in the browse view).
- `stale_va_albums` — albums anchored to the legacy `various_artists` sentinel from older DBs. Should be 0 on a freshly indexed DB; non-zero means a re-index is pending.

### 6. Duration sanity (proxy for the duration-bonus fix)

Once `duration_bonus` is applied to album matching, this should improve:

- For each track with `tracks.mbid` and a non-null local `duration`, the script can't fetch MB length here (no API), so it instead reports the **distribution of local durations of low-score matches**. After fixing, low-score matches should cluster outside ±15s of plausible candidates — but that's only measurable with a live MB call, out of scope for the offline scan.
- Reported here for now: how many tracks have a duration **at all** (the input to the bonus). If many tracks are missing duration, the indexer is dropping it and the fix won't help until that's repaired.

### 7. Filename ground truth

The other six families are *internal-signal* metrics: they judge the DB against itself and have no notion of what's *correct*. This one adds a cheap external reference. When a library uses a flat **`Artist - Title.ext`** filename convention, the filename encodes the intended artist/title independently of the pipeline — so comparing the enriched fields against it surfaces where enrichment **diverged**, which nothing else here can see.

For every track whose basename parses (split on the first ` - `, after stripping a leading track number; filenames whose artist field carries a 4+ digit catalog/id run — e.g. Jamendo `NN-<id>-Artist-Title` — are rejected as non-flat), the enriched artist and title each land in a bucket:

| Bucket | Meaning |
|---|---|
| `exact` | equal up to case/whitespace — enrichment kept the filename value |
| `reformatted` | equal only after diacritic/punct folding — a **canonicalization** (casing/accents, e.g. `THE BEATLES`→`The Beatles`). Confirms the casing work is landing, not breaking. |
| `replaced` | a genuinely different value — the **actionable** signal. Split by whether an MBID is present: an external match that either canonicalized correctly or **corrupted** (the `ABBA → The Singles` class of bug). |
| `unknown` | enrichment left the `unknown_artist` sentinel, or a title that's still a raw filename echo → a **parser miss** on a name it could have extracted. |
| `ambiguous` | (title only) the stem has 2+ ` - ` separators (`Artist - Album - NN - Title`), so the naive first-split title is unreliable — excluded from divergence rather than counted as a false positive. |

**Self-calibration (critical).** The metric is only trustworthy when the library actually follows the flat convention. The scanner computes an **artist agreement rate** = `(exact + reformatted) / parseable`; if it's below **70%** (or fewer than 20 parseable rows), `convention_reliable` is set false, the report prints a warning banner, and the `replaced`/`unknown` **example lists are suppressed** — because on an artist-last, folder-structured, or hyphenated-title library those buckets are dominated by false positives (a title like `Красно-желтые дни` or `Over The Rainbow - Simple Gifts` mis-splits, and enrichment is actually right). Only read the divergence examples when the report says the convention is reliable.

This is what makes a large flat-convention library (e.g. an 8k-track dump of `Artist - Title.mp3`) a genuine evaluation corpus: point the scanner at it and the `replaced`-with-MBID list is a ranked set of suspected bad external matches to inspect.

## Procedure

```bash
python .claude/skills/assess-enrichment/tools/scan.py \
  --db  "${KALINKA_DB:-/home/envel/kalinka/localfiles.db}" \
  --out tmp/enrichment_assess
```

The script:

1. Opens the DB read-only.
2. Computes all metrics in a single pass (~1 sec for ≤10k tracks).
3. Writes `tmp/enrichment_assess/findings.json` (stable schema — diff this across runs).
4. Writes `tmp/enrichment_assess/REPORT.md` (human-readable summary).
5. Prints a one-line headline to stdout so it's pipe-friendly.

## How to use this in a fix workflow

1. **Baseline:** run before touching the enricher. Save `findings.json` as `findings.before.json`.
2. **Fix** something (e.g. add artist name normalization).
3. **Re-enrich** the affected entities (or wipe + re-index for a clean test).
4. **Re-run** the skill. The new `findings.json` should show the targeted counts drop while the others stay roughly stable.
5. `diff` the two JSON files — any unexpected movement is a regression to investigate.

A change is only an improvement if at least one targeted metric drops AND no unrelated metric gets worse.

## Reporting caveats

- The `path_artifact_titles` regex set is heuristic. A real album genuinely named `CD 1` would be flagged — these should be reviewed by hand, not auto-deleted.
- `probable_duplicates` collapses on a permissive normalization. Some legitimate "different artist, similar name" pairs will show up (e.g. "Beach Boys" vs "The Beach Boys" — that's actually a real dup case, but the principle stands). Treat the list as candidates, not facts.
- `folder_splits` doesn't know about discs. A 2-disc album legitimately spans two folders (`CD1`, `CD2`) — those folders won't be flagged because they share a parent. But a deluxe release split into `Standard/` and `Bonus/` siblings *would* be flagged. Inspect before merging.
- Match-score histograms only mean something if the score is actually populated. The current enricher writes `100` for every artist/album match (a known limitation surfaced by this skill).
- The §7 filename-ground-truth buckets are only meaningful when the report says the convention is **reliable** (artist agreement ≥70%). On a folder-structured or artist-last library the section self-suppresses its example lists and prints a warning — heed it; don't mine the buckets for findings there. `ambiguous` (multi-field filenames) and the digit-id rejection keep the reliable case clean, but the reference is still a heuristic: a filename can lie (mistagged at the source), so `replaced`-with-MBID is a list of rows **to inspect**, not confirmed bugs.
- A large external flat-convention library makes a good evaluation corpus even when its audio isn't mounted locally (the scan only reads the DB). Point `--db` at it, e.g. a downloaded `localfiles.db` from another machine.

## Files in this skill

- `SKILL.md` — this file
- `tools/scan.py` — single-pass scanner that writes `findings.json` + `REPORT.md`

If `scan.py` drifts from the DB schema, regenerate it from this SKILL.md rather than hand-patching — the skill is the source of truth.

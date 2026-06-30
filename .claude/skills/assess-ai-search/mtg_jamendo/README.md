# MTG-Jamendo canonical benchmark

**Library-independent** quality benchmark for Kalinka's CLAP search. Ground truth =
official MTG-Jamendo autotagging tags (95 genre / 41 instrument / 59 mood-theme), so
the numbers are reliable and comparable across libraries and over time — unlike the
per-library harness in `../` (`queries.json` + `tools/`), whose ground truth depends
on the local tagger + `artist_labels` curated for one collection.

Use this when you want **"is the CLAP retrieval model good?"** (a stable yardstick for
model / encoder / ranking changes). Use the per-library harness when you want **"how
good is search on THIS user's library?"** (coverage + enrichment included).

## Design

- **Corpus:** stratified MTG-Jamendo subset, frozen in `manifest.jsonl` → reproducible.
- **Audio:** leading ~12s from `mp3d.jamendo.com?trackid=N` (audio/mpeg, no key).
  Range-fetched then ffmpeg→WAV (libsndfile can't read a truncated MP3). CLAP uses ~10s.
- **Embeddings:** production **ONNX** `ClapOnnxModel` → `mtg_bench.db` vec0 index.
- **Queries:** one NL phrasing per target tag; relevance = corpus track carries the tag.
- **Metrics:** P@K, MRR, hit-rate, lift-over-random (exact tag prevalence) per category.

## Run (from repo root)

```bash
PY=packages/kalinka-server/.venv/bin/python
D=.claude/skills/assess-ai-search/mtg_jamendo
$PY $D/select_subset.py     # -> manifest.jsonl, query_tags.json
$PY $D/fetch_audio.py        # -> clips/*.mp3, manifest.fetched.jsonl
$PY $D/embed.py              # -> mtg_bench.db  (production ONNX)
$PY $D/gen_queries.py        # -> queries.json
$PY $D/run_eval.py           # -> results.json + prints the table
$PY $D/run_eval_va.py        # -> results_va.json: baseline vs the mood (V,A) leg
```

`run_eval.py` measures the raw CLAP retrieval model. `run_eval_va.py` additionally
applies the production mood (valence/arousal) leg — `_query_to_va` + the
`knn_search_mood` union + the `(1−w·conf)·clap + (w·conf)·mood` blend, using the
deployed `va_head_v*.onnx` / `mood_index_v*.npz` — and prints a baseline→VA
before/after table (point `VA_ARTIFACT_DIR` at the dir holding those artifacts).

## Knobs (env vars)

| var | default | effect |
|---|---|---|
| `MTG_BENCH_DIR` | `<repo>/tmp/mtg_jamendo_eval` | where artifacts (clips, db, json) live |
| `N_TRACKS` | 600 | corpus size (e.g. 4000 for tighter per-tag stats) |
| `PER_TAG` | 40 | max tracks sampled per target tag |
| `KALINKA_MODEL_DIR` | `~/kalinka/models` | CLAP ONNX model dir |
| `CLIP_BYTES` | 450000 | bytes fetched per track |
| `EVAL_K` | 10 | top-K for metrics |

Scale up: `N_TRACKS=4000 PER_TAG=120 $PY $D/select_subset.py` then re-run fetch→eval.
Artifacts default to `tmp/` (gitignored); only these scripts are committed.

## Notes

- **Audio leg only** — MTG tracks lack rich title/artist text, so no text vector index
  (production retrieves over the audio index anyway).
- **Genre/instrument retrieve well; abstract mood/theme poorly** — a CLAP property
  reproduced cleanly here (see a generated `REPORT.md` under `MTG_BENCH_DIR`).
- Requires `ffmpeg` on PATH, the `sqlite_vec` + `requests` + `numpy` venv, and the ONNX
  CLAP model files.

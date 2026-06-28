---
name: assess-ai-search
description: Measure precision / recall / MRR of Kalinka's CLAP-backed `ai_search` against the local `localfiles.db`. Use when the user asks to "evaluate the AI search", "benchmark CLAP", "check semantic-search quality", "measure retrieval precision/recall", or wants improvement suggestions for the embedding-based search. Generates a ~100-query benchmark, builds ground truth from artist heuristics + predicted tags + album metadata, runs the queries against both the audio-side (`vec_tracks_clap`) and text-side (`vec_tracks_clap_text`) vector indexes, and writes a Markdown report under `tmp/ai_search_eval/`.
---

# assess-ai-search

This skill has **two** benchmarks — pick by what you're asking:

| You want to know… | Use | Ground truth | Where |
|---|---|---|---|
| Is the CLAP **retrieval model** good? (stable yardstick for model/encoder/ranking changes) | **MTG-Jamendo canonical benchmark** | official human-curated tags — reliable, library-independent | [`mtg_jamendo/`](mtg_jamendo/README.md) |
| How good is search on **this user's library**? (coverage + enrichment included) | per-library harness (below) | local tagger + `album_genre` + curated `artist_labels` — only reliable on the dev library | `queries.json` + `tools/` |

The per-library numbers are a conservative floor on an arbitrary library (sparse tags,
`artist_labels` curated for one collection); the MTG-Jamendo benchmark is the
trustworthy absolute measure. Both encode with the production ONNX encoder.

---

Reproducible quality benchmark for the AI search in `kalinka-plugin-localfiles`. The skill assumes:

- DB at `~/kalinka/var/lib/kalinka/localfiles.db` (the dev-run active DB; a stale float-vector copy may linger at `~/kalinka/localfiles.db`) (override with `KALINKA_DB`)
- Embeddings already computed (`embedding_clap_audio`, `embedding_clap_text`, `tags_predicted` are populated)
- The **ONNX** CLAP wrapper (`packages/kalinka-plugin-localfiles/src/kalinka_plugin_localfiles/embedder/clap_onnx.py`) is loadable and its model files (`clap_text_encoder.onnx`, `clap_tokenizer.json`) are present in `KALINKA_MODEL_DIR` (default `~/kalinka/models`). **This is mandatory** — the stored vectors are ONNX-encoded; see the encoder note in "Reporting caveats".

The two vector indexes have **different semantics** and the skill measures both:

| Index | Side | What is stored | Used by |
|---|---|---|---|
| `vec_tracks_clap` | audio | CLAP audio embedding of the first 10s | `test_ai_search.py` (canonical CLAP retrieval) |
| `vec_tracks_clap_text` | text | CLAP **text** embedding of the track's title/artist/album string | comparison only (FTS handles metadata lookups) |

Production `searcher._knn_leg` queries the **audio** index (`knn_search_audio` → `vec_tracks_clap`): CLAP is contrastively trained text↔audio, so text-query → audio-embedding is the canonical retrieval direction.

A text query is encoded with the CLAP text encoder and KNN'd against both. Reporting both makes the audio↔text vs text↔text gap visible — that single number drives most of the "should we switch indexes?" decision.

## Procedure

Run all steps in order. Do not skip step 1 or 2 — the report is only meaningful if ground truth was rebuilt against the current DB.

### 1. Sanity-check the DB

```bash
sqlite3 "${KALINKA_DB:-$HOME/kalinka/var/lib/kalinka/localfiles.db}" "
SELECT
  (SELECT COUNT(*) FROM tracks) AS tracks,
  (SELECT COUNT(*) FROM tracks WHERE embedding_clap_audio IS NOT NULL) AS audio_embedded,
  (SELECT COUNT(*) FROM tracks WHERE embedding_clap_text  IS NOT NULL) AS text_embedded,
  (SELECT COUNT(*) FROM tracks WHERE tags_predicted IS NOT NULL AND tags_predicted != '') AS tagged,
  (SELECT COUNT(*) FROM vec_tracks_clap)      AS vec_audio_rows,
  (SELECT COUNT(*) FROM vec_tracks_clap_text) AS vec_text_rows;
"
```

If audio/text coverage is <90%, **flag it in the report** — low coverage caps recall mechanically and is the first thing to fix before tuning weights.

### 2. Build ground truth

Run `tools/build_ground_truth.py` (bundled with this skill). It joins `tracks`, `albums`, `artists` and writes `tmp/ai_search_eval/ground_truth.jsonl` with one record per track:

```json
{
  "track_id": "...", "title": "...", "artist": "...", "album": "...",
  "album_genre": "rock",                      // raw albums.genre, lowercased
  "year": 1985,
  "predicted_top_genres": ["rock", "pop"],    // tags_predicted.genres[].label split on '---', top-level
  "predicted_subgenres":  ["prog rock"],
  "mood_cluster": 2,
  "danceability": 0.21,
  "artist_labels": ["rock","prog rock","psychedelic","classic rock"]  // from queries.json:artist_labels
}
```

Artist labels come from `queries.json → artist_labels` — a curated map of well-known artists in this specific library (Pink Floyd, Vangelis, Daft Punk, VNV Nation, Кино, etc.). These exist because predicted tags are sparse (~30% of tracks have non-empty genres) and `albums.genre` is dirty. The map is intentionally narrow: only include an artist when their canonical genre is unambiguous, otherwise leave them to fall back on predicted tags.

### 3. Run the benchmark

```bash
python .claude/skills/assess-ai-search/tools/evaluate.py \
  --db        "${KALINKA_DB:-$HOME/kalinka/var/lib/kalinka/localfiles.db}" \
  --queries   .claude/skills/assess-ai-search/queries.json \
  --truth     tmp/ai_search_eval/ground_truth.jsonl \
  --k         10 \
  --indexes   audio,text \
  --out       tmp/ai_search_eval/results.json
```

Per query and per index the script reports:

- **Precision@K** — fraction of top-K results that are relevant per the query's rule
- **Recall@K** — `relevant_in_topK / min(K, total_relevant_in_library)` (capped recall — see "Reporting caveats")
- **MRR** — mean reciprocal rank of the first relevant hit (0 if none in top K)
- **Hit-Rate@K** — fraction of queries with ≥1 relevant in top K (more informative than recall when relevant set is tiny)
- **Distance distribution** — min/median/max KNN distance of relevant vs non-relevant hits, used to suggest a similarity threshold

### 4. Compose the report

Read `results.json` and write `tmp/ai_search_eval/REPORT.md` with the sections below. Each table cell that's worse than the "good" threshold should be tagged `(low)` and link to the per-query JSON so the reader can inspect failures.

| Section | Content | Good if |
|---|---|---|
| Coverage | embedding + tag coverage from step 1 | ≥95% |
| Headline | mean P@10, R@10, MRR, Hit@10 — audio vs text | P@10 ≥ 0.40, Hit@10 ≥ 0.70 |
| By category | one row per category in `queries.json` (genre, subgenre, mood, energy, instrument, activity, era, abstract) | |
| Top failures | 10 queries with lowest P@10 (audio + text), with their top-3 wrong hits | |
| Top wins | 10 queries with highest P@10 | |
| Audio vs text gap | queries where the two indexes disagree the most | |
| Improvement suggestions | filled-in checklist from "Improvement suggestions" below | |

### 5. Suggest improvements

After looking at the numbers, walk the checklist below and write findings under the corresponding heading. **Only include items the data supports** — empty headings are fine, speculative ones are not.

**A. Pipeline / coverage**

- [ ] Audio/text embedding coverage <95% → schedule a one-shot embedder pass before re-evaluating.
- [ ] Tag coverage <80% or empty-`genres` fraction >40% → the AutoTagger threshold is too high. The current `tags_predicted` blobs show many tracks with `genres: []` even when album metadata clearly says "Rock" — relax `searcher.tags.*` thresholds or swap the tagger.
- [ ] `vec_tracks_clap` row count ≠ `vec_tracks_clap_text` row count → index drift, rebuild the lagging side.

**B. Index choice (audio vs text)**

CLAP is contrastively trained text↔audio, so a text query naturally retrieves over the **audio** index — which is what production already queries (`searcher._knn_leg → knn_search_audio`). The benchmark confirms audio > text on mood / activity / abstract / instrument (the timbre-driven categories); text only edges ahead on subgenre / era (metadata-word driven, which FTS already covers). So keep the audio leg; do **not** route NL queries through the text vector index — it's lexical and degenerates on sparse metadata (many tracks with empty/"Untitled" text collapse to one near-identical embedding).

The text index is plausibly better for queries that name an artist / album / language explicitly — those should be handled by FTS anyway. Confirm with the per-category gap before recommending the flip.

**C. Re-ranker weights** (`SearcherConfig.weight_fts / weight_knn / weight_genre / weight_mood / weight_danceability`, currently 0.35 / 0.30 / 0.20 / 0.10 / 0.05)

- [ ] Genre queries (`queries.category == "genre"`) underperform → bump `weight_genre`.
- [ ] Mood queries underperform → bump `weight_mood`, but also check whether the mood-cluster index is meaningful (only 3-4 distinct cluster ids in the DB suggests the clustering is too coarse).
- [ ] Activity / abstract queries underperform but mood/genre are fine → the issue is query understanding, not re-rank weights; see (D).

**D. Query understanding**

- [ ] Russian queries / Cyrillic-named tracks (Кино, Ленинград, Аквариум, Ддт, Борис Гребенщиков, Nautilus Pompilius) consistently miss → CLAP is English-trained; recommend an FTS fallback for non-ASCII queries or translate queries before encoding.
- [ ] Compound queries ("calm acoustic", "epic orchestral score") have lower P@10 than their atomic parts ("calm", "acoustic") → recommend a lightweight LLM query-rewrite step that emits both the original and component queries, then RRF-fuses the results.
- [ ] Negations / exclusions (any "no X" query) consistently fail → CLAP can't represent negation; document as a known limitation.

**E. Model upgrades**

- [ ] Many high-confidence false positives clustered around long-form tracks → CLAP-htsat-unfused only looks at the first 10s; try `clap-htsat-fused` (variable-length) or average embeddings over multiple chunks.
- [ ] Genre tagger predictions are noisy (most tracks get `electronic---ambient` regardless of actual genre — visible in `SELECT json_extract(tags_predicted,'$.genres[0].label'), COUNT(*) FROM tracks GROUP BY 1 ORDER BY 2 DESC`) → swap to a stronger tagger (MERT-95M fine-tuned, or a small in-house classifier trained on Jamendo + MagnaTagATune).
- [ ] If P@10 < 0.30 across the board → fine-tune CLAP on a few thousand library tracks with weak supervision from album genre + artist labels.

**F. Evaluation methodology**

- [ ] If many queries report `total_relevant_in_library == 0` → ground truth is too narrow; widen `artist_labels` or relax the rule predicate.
- [ ] If P@10 and Hit@10 disagree wildly → the relevant set is too small for precision to be stable; report Hit-Rate primarily.

## Reporting caveats

### Corpus skew distorts raw P@10 — always report lift-over-random

When the library is dominated by a single class (e.g., 60% electronic, or 90% music with mood_cluster=2), queries whose relevance rule overlaps that class get high P@10 *for free*. A random retriever scores `total_relevant / library_size` per query in expectation.

**Always compute and report:**
- `random_baseline_P@10 = total_relevant / N` (per query)
- `lift = observed_P@10 − random_baseline_P@10`
- A query with `P@10 = 0.90` and `random_baseline = 0.45` has only +0.45 lift — the model captured half the apparent headroom. A query with `P@10 = 0.70` and `random_baseline = 0.04` has +0.66 lift and is a genuine retrieval win.

Queries with `negative lift` are the diagnostic gold — they mean the retriever is doing *worse* than throwing darts. Typically: highly-prevalent relevance classes where the retriever's top-10 happens to cluster in the small non-relevant minority. Surface those in the report.

Use `tools/analyze.py` (bundled) to compute lift on an existing `results.json` / `results.endpoint.json` without re-running the encoder.

### Recall@K is structurally capped — report what's actually achievable

At K=10 with N=519 tracks and an average class size of ~300 (typical for mood/era queries), the *mathematical maximum* uncapped R@10 is `K / total_relevant ≈ 3%`. Reporting "R@10 = 0.05" reads like a failure but is within touching distance of the theoretical ceiling — the metric is broken, not the model.

**At small library scale, do all three:**
- **Capped R@10** = `n_rel_in_topK / min(K, total_relevant)` — what `evaluate.py` reports. Equivalent to P@10 when total_relevant ≥ K (almost always true in this library), so largely redundant.
- **Uncapped R@10** = `n_rel_in_topK / total_relevant` — honest but mechanically tiny for prevalent classes.
- **Recall-at-ceiling** = `uncapped_R@K × total_relevant / K` — fraction of the *achievable* recall headroom captured. Bounded in [0, 1] regardless of class size. This is the metric to compare across queries with different class sizes.

For meaningful aggregate recall, raise K to `min(50, total_relevant)` or report `R@50` separately. At K=50 the structural cap on uncapped recall is loose enough that the number reflects model quality, not class size.

### Other gotchas

- Ground truth is heuristic, not curated. A "wrong" hit might actually be relevant — sample 5-10 disagreements by ear when the numbers look surprising. Do not retune weights against a P@10 delta smaller than 0.05 — it's within the noise of the labelling heuristic.
- **Encode with ONNX, not `laion_clap`.** The stored `vec_tracks_clap*` vectors are generated by the ONNX encoder (`ClapOnnxModel`). `laion_clap` lives in a **different embedding space** — querying ONNX-audio vectors with laion_clap-text vectors is near-random (cross-space KNN, distances ~1.31, ~½ the precision/MRR/lift). This is **not** a 4th-decimal difference. `evaluate.py` defaults to the ONNX encoder; `KALINKA_FORCE_LAION=1` switches to laion_clap only for an explicit, loudly-warned comparison. Older `results.*.json` / baseline reports in `tmp/ai_search_eval/` predate this fix and were produced with laion_clap — treat them as invalid.
- 6 queries in `queries.json` may have `total_relevant == 0` if the library lacks those genres entirely. These queries always score 0 and drag the mean P@10 down by ~6 pp. Exclude them from the headline (filter `total_relevant > 0`) or annotate them with `"corpus_ood": true` in `queries.json`.

## Files in this skill

- `SKILL.md` — this file
- `queries.json` — 100 benchmark queries grouped by category, with relevance rules and the `artist_labels` lookup
- `tools/build_ground_truth.py` — joins the DB into `ground_truth.jsonl`
- `tools/evaluate.py` — encodes queries, runs KNN against both vec indexes directly, computes raw metrics
- `tools/evaluate_endpoint.py` — same metrics but hits the live `/ai_search` endpoint (full FTS + KNN + re-rank pipeline)
- `tools/analyze.py` — post-hoc analysis: lift-over-random, recall-at-ceiling, real-wins vs corpus-skew artifacts
- `mtg_jamendo/` — **canonical library-independent benchmark** (MTG-Jamendo, official tags as ground truth). See [`mtg_jamendo/README.md`](mtg_jamendo/README.md); pipeline is `select_subset.py → fetch_audio.py → embed.py → gen_queries.py → run_eval.py`, artifacts default to `tmp/mtg_jamendo_eval/`.

If a script is missing or out of date relative to the DB schema, regenerate it from this SKILL.md rather than hand-patching — the skill is the source of truth.

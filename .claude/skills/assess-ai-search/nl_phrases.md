# Natural-language evaluation phrases for `ai_search`

Hand-curated free-text queries for **qualitative** evaluation of the CLAP-backed
semantic search (the kind a user actually types). These complement the structured
`queries.json` benchmark: where `queries.json` measures P@K/recall against
heuristic ground truth, this set is for *eyeballing* whether the top 30–50 KNN
neighbours actually feel like the request.

Each phrase is what the user types verbatim. The searcher's `parse_query` will
strip genre/mood/filler tokens before the FTS leg; the CLAP **text encoder** sees
the raw phrase. These are deliberately phrased the way a human asks — moods,
activities, vibes — not artist/album lookups (those belong to FTS).

## Phrase set

| # | phrase | category | what a good result looks like |
|---|--------|----------|-------------------------------|
| 1 | something melancholic for tonight | mood | sad / introspective / slow, minor-key |
| 2 | upbeat happy summer vibes | mood | bright, major-key, energetic pop |
| 3 | dark and aggressive | mood | heavy, distorted, intense |
| 4 | classical music | genre | orchestral / piano / chamber works |
| 5 | smooth jazz | genre | mellow jazz, sax, lounge |
| 6 | heavy metal | genre | distorted guitars, double kick, screams |
| 7 | ambient electronic soundscapes | genre | atmospheric, beatless or downtempo synth |
| 8 | music for deep focus and studying | activity | calm, non-distracting, instrumental |
| 9 | energetic workout music | activity | fast tempo, driving beat |
| 10 | relaxing music to fall asleep to | activity | slow, soft, quiet |
| 11 | epic orchestral film score | abstract | cinematic, dramatic, building |
| 12 | acoustic guitar singer songwriter | instrument | acoustic, vocal-forward, intimate |

## How to run

The harness (`tools/nl_eval.py`, generated alongside this file) encodes each
phrase with the **exact** CLAP ONNX text encoder used by the embedder
(`clap_onnx.ClapOnnxModel.get_text_embedding`), then KNNs:

- `vec_tracks_clap` (**audio** index) — the production retrieval direction
  (`searcher._knn_leg → knn_search_audio`); text↔audio is CLAP's trained pairing.
- `vec_tracks_clap_text` (**text** index) — for the audio-vs-text gap.

Results (top 40) are dumped with title/artist/album/predicted-genre/mood so the
match quality can be judged by reading them.

> Note: production's `_knn_leg` queries the **audio** index. (The skill's
> `SKILL.md` table is stale — it says `knn_search_text`; the code now uses
> `knn_search_audio`.)

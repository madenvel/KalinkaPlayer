"""
Search worker — Essentia tag pipeline + FTS5 indexing + search ranking.

Runs as a separate long-lived subprocess (same pattern as embedder.py).

Offline work:
  1. Predicts genre, mood and danceability tags via Essentia-TensorFlow
     (EffNet for genre, VGGish for mood/danceability).
  2. Populates FTS5 index from enriched + tagged tracks.
  3. Nudges the embedder process when tag stages complete so CLAP jobs
     can be scheduled.

Online work:
  - Receives search queries via IPC queues.
  - Parses NL queries into structured constraints.
  - Retrieves FTS5 candidates.
  - Re-ranks using predicted genre/mood/danceability tags.
  - Derives album/artist results from track hits.

ML dependencies are NOT listed in pyproject.toml — installed on demand.
Model files are auto-downloaded to config.searcher.model_dir if missing.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import logging.handlers
import math
import multiprocessing
import os
import queue
import re
import signal
import time
import urllib.request
from collections import defaultdict
from typing import Optional

from ..config_model import LocalFilesConfig
from ..embedding_utils import decode_embedding
from ..pip_utils import ensure_package
from ..worker_utils import set_proc_title, sleep_interruptible
from .best_match import Entity, assemble_best_match
from .genre_labels import label_for_index
from .query_parser import ParsedQuery, parse_query
from .searcher_db import AsyncSearcherDb

logger = logging.getLogger(__name__.split(".")[-1])


# ---------------------------------------------------------------------------
# On-demand pip install (process-local config)
# ---------------------------------------------------------------------------

_PIP_SPECS: dict[str, str] = {
    "essentia": "essentia-tensorflow",
    "essentia_tensorflow": "essentia-tensorflow",
    "numpy": "numpy",
}

_IMPORT_NAME_ALIASES: dict[str, str] = {
    "essentia_tensorflow": "essentia",
}


def _ensure_package(import_name: str) -> bool:
    return ensure_package(import_name, _PIP_SPECS, _IMPORT_NAME_ALIASES)


# ---------------------------------------------------------------------------
# Numpy — lazy module-level reference
# ---------------------------------------------------------------------------

np = None


def _ensure_numpy() -> bool:
    global np
    if np is not None:
        return True
    if not _ensure_package("numpy"):
        logger.error("numpy unavailable; searcher tag pipeline cannot run")
        return False
    import numpy

    np = numpy
    return True


# Filler words ignored when scoring a query's mood "purity" (_query_to_va).
# Genre/instrument nouns are deliberately excluded — that's content for CLAP.
_FILLER_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "by", "with", "from", "into", "my", "me", "i", "you", "your", "we", "us",
    "it", "its", "this", "that", "these", "those", "some", "something",
    "anything", "like", "want", "need", "give", "play", "playing", "song",
    "songs", "music", "track", "tracks", "tune", "tunes", "sound", "sounds",
    "playlist", "vibe", "vibes", "mood", "feeling", "feel", "get", "got", "im",
    "am", "are", "is", "be", "now", "tonight", "today", "day", "night", "time",
    "really", "very", "more", "bit", "little", "kinda", "sorta", "stuff",
})


# ---------------------------------------------------------------------------
# Model auto-download
# ---------------------------------------------------------------------------

_MODEL_URLS: dict[str, str] = {
    "effnet": "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "genre": "https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb",
    "vggish": "https://essentia.upf.edu/models/feature-extractors/vggish/audioset-vggish-3.pb",
    "mood_mirex": "https://essentia.upf.edu/models/classification-heads/moods_mirex/moods_mirex-audioset-vggish-1.pb",
    "danceability": "https://essentia.upf.edu/models/classification-heads/danceability/danceability-audioset-vggish-1.pb",
}


def _ensure_model_file(
    name: str, configured_path: str, model_dir: str
) -> Optional[str]:
    """Return path to model file, downloading into model_dir if necessary."""
    # Both inputs come from user config and may contain ``~`` — expand
    # so os.path.isfile / os.makedirs see absolute paths. Without this,
    # ``model_dir = "~/kalinka/models"`` causes a literal ``~`` directory
    # to be created under the server's CWD (which then masks future
    # "delete cached models" migrations).
    if configured_path:
        configured_path = os.path.expanduser(configured_path)
    model_dir = os.path.expanduser(model_dir)

    if configured_path and os.path.isfile(configured_path):
        return configured_path

    url = _MODEL_URLS.get(name)
    if not url:
        logger.error("No download URL for model '%s'", name)
        return None

    filename = url.rsplit("/", 1)[-1]
    dest = os.path.join(model_dir, filename)
    if os.path.isfile(dest):
        return dest

    os.makedirs(model_dir, exist_ok=True)
    logger.info("Downloading model '%s' from %s …", name, url)
    try:
        urllib.request.urlretrieve(url, dest)
        logger.info("Model '%s' saved to %s", name, dest)
        return dest
    except Exception as e:
        logger.error("Failed to download model '%s': %s", name, e)
        return None


# ---------------------------------------------------------------------------
# SearchWorker
# ---------------------------------------------------------------------------


class SearchWorker:
    def __init__(
        self,
        config: LocalFilesConfig,
        db: AsyncSearcherDb,
        text_encode_request_queue: Optional[multiprocessing.Queue] = None,
        text_encode_response_queue: Optional[multiprocessing.Queue] = None,
    ):
        self.config = config
        self.db = db
        # Essentia models (lazy-loaded per stage, unloaded after idle)
        self._effnet = None
        self._genre_cls = None
        self._vggish = None
        self._mood_cls = None
        self._dance_cls = None
        self._tags_load_attempted_at: float = 0.0
        # IPC queues for CLAP text encoding (served by the embedder process)
        self._text_encode_request_queue = text_encode_request_queue
        self._text_encode_response_queue = text_encode_response_queue
        # Mood index (words, va[M,2], text_emb[M,512]); lazy-loaded, retried.
        self._mood_index: Optional[tuple] = None
        self._mood_load_attempted_at: float = 0.0

    # ------------------------------------------------------------------
    # Model loading / unloading
    # ------------------------------------------------------------------

    def _load_tag_models(self):
        """Load all tag prediction models (EffNet + VGGish + classifiers)."""
        if self._effnet is not None:
            return  # already loaded
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.searcher
        if not cfg.tags.enabled or cfg.tags.current_version == 0:
            return
        if not _ensure_numpy():
            return
        if not _ensure_package("essentia"):
            logger.warning("essentia-tensorflow unavailable; tag prediction disabled")
            return
        try:
            model_dir = cfg.model_dir
            effnet_path = _ensure_model_file("effnet", cfg.tags.effnet_path, model_dir)
            genre_path = _ensure_model_file("genre", cfg.tags.genre_path, model_dir)
            vggish_path = _ensure_model_file("vggish", cfg.tags.vggish_path, model_dir)
            mood_path = _ensure_model_file(
                "mood_mirex", cfg.tags.mood_mirex_path, model_dir
            )
            dance_path = _ensure_model_file(
                "danceability", cfg.tags.danceability_path, model_dir
            )
            import essentia.standard as es

            if effnet_path and genre_path:
                self._effnet = es.TensorflowPredictEffnetDiscogs(
                    graphFilename=effnet_path, output="PartitionedCall:1"
                )
                self._genre_cls = es.TensorflowPredict2D(
                    graphFilename=genre_path,
                    input="serving_default_model_Placeholder",
                    output="PartitionedCall:0",
                )
                logger.info("Genre model (EffNet) loaded")
            else:
                logger.warning("Genre model files unavailable; genre prediction disabled")

            if vggish_path:
                self._vggish = es.TensorflowPredictVGGish(
                    graphFilename=vggish_path, output="model/vggish/embeddings"
                )
                if mood_path:
                    self._mood_cls = es.TensorflowPredict2D(
                        graphFilename=mood_path,
                        input="serving_default_model_Placeholder",
                        output="PartitionedCall",
                    )
                    logger.info("Mood model (VGGish) loaded")
                else:
                    logger.warning("Mood model file unavailable; mood disabled")
                if dance_path:
                    self._dance_cls = es.TensorflowPredict2D(
                        graphFilename=dance_path,
                        input="model/Placeholder",
                        output="model/Softmax",
                    )
                    logger.info("Danceability model (VGGish) loaded")
                else:
                    logger.warning("Danceability model file unavailable")
            else:
                logger.warning("VGGish model unavailable; mood/danceability disabled")
        except Exception as e:
            logger.warning("Tag model loading failed: %s", e)

    def _unload_models(self):
        if self._effnet is None and self._vggish is None:
            return
        self._effnet = None
        self._genre_cls = None
        self._vggish = None
        self._mood_cls = None
        self._dance_cls = None
        self._tags_load_attempted_at = 0.0
        gc.collect()
        logger.info("Tag models unloaded")

    # ------------------------------------------------------------------
    # CLAP text encoding via IPC (served by embedder process)
    # ------------------------------------------------------------------

    def _encode_query_text(self, query: str) -> Optional[bytes]:
        """Encode a text query with CLAP via IPC to the embedder process."""
        if (
            self._text_encode_request_queue is None
            or self._text_encode_response_queue is None
        ):
            return None
        try:
            self._text_encode_request_queue.put({"query": query})
            resp = self._text_encode_response_queue.get(timeout=30)
            return resp.get("blob")
        except Exception as e:
            logger.warning("CLAP text encoding IPC failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Mood (valence/arousal) query mapping
    # ------------------------------------------------------------------

    def _load_mood_index(self) -> Optional[tuple]:
        """Load (words, va, text_emb) from mood_index.npz; download if needed.

        Best-effort and retried (~5 min) so a transient download failure
        doesn't disable mood ranking for the process lifetime. Returns None
        until available, in which case mood ranking degrades to pure CLAP.
        """
        if self._mood_index is not None:
            return self._mood_index
        if time.monotonic() - self._mood_load_attempted_at < 300:
            return None
        self._mood_load_attempted_at = time.monotonic()
        if not _ensure_numpy():
            return None
        try:
            # The mood index lives with the CLAP artifacts; reuse their
            # downloader (distinct from the tag-model _ensure_model_file here).
            from ..embedder.clap_onnx import _ensure_model_file as _ensure_clap
            model_dir = os.path.expanduser(self.config.embedder.model_dir)
            path = _ensure_clap("mood_index", model_dir)
            if not path:
                return None
            d = np.load(path, allow_pickle=True)
            self._mood_index = (
                [str(w) for w in d["words"]],
                d["va"].astype(np.float32),
                d["text_emb"].astype(np.float32),
            )
            logger.info("Mood index loaded (%d words)", len(self._mood_index[0]))
        except Exception as e:
            logger.warning("Mood index load failed (mood ranking off): %s", e)
            self._mood_index = None
        return self._mood_index

    def _query_to_va(
        self, query: str, query_blob: Optional[bytes]
    ) -> tuple[Optional[tuple[float, float]], float]:
        """Map a query to a target (valence, arousal) + confidence in [0,1].

        Keyword spotting first (a literal mood word -> confidence 1.0), then a
        CLAP-text nearest-neighbour fallback over the mood vocabulary. Returns
        (None, 0.0) for non-mood queries so ranking stays pure CLAP. No LLM.
        """
        idx = self._load_mood_index()
        if idx is None:
            return None, 0.0
        words, va, emb = idx
        mcfg = self.config.searcher.mood

        # 1) Keyword spotting — literal mood word(s) present in the query.
        tokens = set(re.findall(r"[a-z]+", query.lower()))
        hits = [i for i, w in enumerate(words) if w in tokens]
        if hits:
            tv = float(np.mean([va[i][0] for i in hits]))
            ta = float(np.mean([va[i][1] for i in hits]))
            # Confidence = mood purity (share of non-filler words that are mood
            # words): pure mood -> 1.0; "melancholic piano" -> 0.5 (CLAP keeps piano).
            matched = {words[i] for i in hits}
            content = [t for t in tokens if len(t) >= 3 and t not in _FILLER_WORDS]
            mood_share = sum(t in matched for t in content) / len(content) \
                if content else 1.0
            return (tv, ta), float(mood_share)

        # 2) CLAP-text nearest-neighbour fallback.
        if not mcfg.nn_fallback or query_blob is None:
            return None, 0.0
        q = decode_embedding(query_blob).astype(np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return None, 0.0
        sims = emb @ (q / qn)
        order = np.argsort(-sims)[: mcfg.nn_top_k]
        top_cos = float(sims[order[0]])
        if top_cos < mcfg.nn_threshold:
            return None, 0.0  # query isn't mood-like -> pure CLAP
        w = np.clip(sims[order], 0.0, None)
        if w.sum() <= 0:
            return None, 0.0
        tv = float((va[order, 0] * w).sum() / w.sum())
        ta = float((va[order, 1] * w).sum() / w.sum())
        # Confidence rises from 0 at the threshold to 1 at perfect similarity.
        conf = (top_cos - mcfg.nn_threshold) / (1.0 - mcfg.nn_threshold)
        return (tv, ta), float(min(1.0, max(0.0, conf)))

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _predict_all_tags(self, file_path: str) -> dict:
        """Predict genre, mood, and danceability in a single pass.

        Decodes audio once.  Runs EffNet for genre, then VGGish once and
        passes its embeddings to both the mood and danceability classifiers.
        Returns a dict with keys: genres, mood_cluster, danceability.
        Missing keys indicate that the corresponding model was unavailable.
        """
        result: dict = {}
        try:
            import essentia.standard as es

            t0 = time.monotonic()
            loader = es.MonoLoader(filename=file_path, sampleRate=16000)
            audio = loader()
            # Truncate to first 60s — diminishing returns for genre/mood
            # classification beyond that, and inference cost scales linearly.
            max_samples = 60 * 16000
            if len(audio) > max_samples:
                audio = audio[:max_samples]
            t_decode = time.monotonic() - t0
            logger.info(
                "  audio decode: %.3fs (%d samples, %.1fs duration)",
                t_decode,
                len(audio),
                len(audio) / 16000,
            )

            # --- Genre (EffNet backbone) ---
            if self._effnet is not None and self._genre_cls is not None:
                try:
                    t1 = time.monotonic()
                    effnet_embeddings = self._effnet(audio)
                    t_effnet = time.monotonic() - t1
                    logger.info("  effnet backbone: %.3fs", t_effnet)

                    t1 = time.monotonic()
                    genre_activations = self._genre_cls(effnet_embeddings)
                    t_genre_cls = time.monotonic() - t1
                    logger.info("  genre classifier: %.3fs", t_genre_cls)

                    genre_mean = genre_activations.mean(axis=0)
                    cfg = self.config.searcher.tags
                    top_genres = []
                    sorted_indices = genre_mean.argsort()[::-1]
                    for idx in sorted_indices:
                        score = float(genre_mean[idx])
                        if score < cfg.min_confidence:
                            break
                        if len(top_genres) >= cfg.top_genres:
                            break
                        top_genres.append(
                            {"label": label_for_index(idx), "score": round(score, 3)}
                        )
                    result["genres"] = top_genres
                except Exception as e:
                    logger.debug("Genre extraction error: %s", e)

            # --- VGGish embeddings (shared by mood + danceability) ---
            if self._vggish is not None:
                try:
                    t1 = time.monotonic()
                    vggish_embeddings = self._vggish(audio)
                    t_vggish = time.monotonic() - t1
                    logger.info("  vggish backbone: %.3fs", t_vggish)

                    if self._mood_cls is not None:
                        try:
                            t1 = time.monotonic()
                            mood_activations = self._mood_cls(
                                vggish_embeddings
                            ).mean(axis=0)
                            logger.info(
                                "  mood classifier: %.3fs", time.monotonic() - t1
                            )
                            result["mood_cluster"] = int(mood_activations.argmax())
                        except Exception as e:
                            logger.debug("Mood prediction error: %s", e)

                    if self._dance_cls is not None:
                        try:
                            t1 = time.monotonic()
                            dance_activations = self._dance_cls(
                                vggish_embeddings
                            ).mean(axis=0)
                            logger.info(
                                "  dance classifier: %.3fs", time.monotonic() - t1
                            )
                            result["danceability"] = round(
                                float(dance_activations[0])
                                if len(dance_activations) > 0
                                else 0.0,
                                3,
                            )
                        except Exception as e:
                            logger.debug("Danceability prediction error: %s", e)
                except Exception as e:
                    logger.debug("VGGish inference error: %s", e)

            logger.info(
                "Tag inference total: %.3fs (genre=%s mood=%s dance=%s)",
                time.monotonic() - t0,
                "genres" in result,
                "mood_cluster" in result,
                "danceability" in result,
            )
        except Exception as e:
            logger.warning("Tag prediction failed for %s: %s", file_path, e)

        return result

    # ------------------------------------------------------------------
    # Tag batch processor
    # ------------------------------------------------------------------

    async def _process_tags_batch(self) -> bool:
        """Process a batch of unified tag jobs (genre + mood + danceability).

        Each track is decoded once; VGGish embeddings are computed once and
        shared between mood and danceability classifiers.
        """
        cfg = self.config.searcher
        batch = await self.db.claim_batch("tags", cfg.batch_size_tags)
        if not batch:
            return False

        completed = []
        for job in batch:
            track_id = job["entity_id"]
            file_path = await self.db.get_file_path_for_track(track_id)
            if file_path is None:
                await self.db.fail_job(
                    job["id"], "track not found", cfg.max_job_attempts
                )
                continue

            tags = self._predict_all_tags(file_path)
            tags_json = json.dumps(tags) if tags else json.dumps({})
            try:
                await self.db.complete_tags_job(job["id"], track_id, tags_json)
                completed.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed:
            logger.info("Tags written for %d tracks", len(completed))
        return True

    # ------------------------------------------------------------------
    # Ranking helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_tag_components(
        parsed: ParsedQuery, track_tags: dict | None
    ) -> tuple[float, float, float]:
        """Compute the genre / mood / danceability score components for
        a single track against the parsed query.

        Each value is in [0, 1]; absent tags or unconfigured query
        constraints yield 0.0. Pulled out of ``_score_track`` so the
        per-query summary logging can reuse the exact same logic
        rather than duplicating the matching rules.
        """
        if not track_tags:
            return 0.0, 0.0, 0.0

        genre_score = 0.0
        if parsed.genres:
            t_genres = track_tags.get("genres") or []
            genre_labels = " ".join(
                g.get("label", "") for g in t_genres if isinstance(g, dict)
            ).lower()
            matched = sum(1 for qg in parsed.genres if qg in genre_labels)
            genre_score = matched / len(parsed.genres)

        mood_score = 0.0
        if parsed.mood_clusters:
            t_mood = track_tags.get("mood_cluster")
            if t_mood is not None and t_mood in parsed.mood_clusters:
                mood_score = 1.0

        dance_score = 0.0
        t_dance = track_tags.get("danceability")
        if t_dance is not None:
            dance_ok = True
            if (
                parsed.min_danceability is not None
                and t_dance < parsed.min_danceability
            ):
                dance_ok = False
            if (
                parsed.max_danceability is not None
                and t_dance > parsed.max_danceability
            ):
                dance_ok = False
            if dance_ok and (
                parsed.min_danceability is not None
                or parsed.max_danceability is not None
            ):
                dance_score = 1.0

        return genre_score, mood_score, dance_score

    def _score_track(
        self,
        parsed: ParsedQuery,
        knn_norm: float,
        track_tags: dict | None,
        has_knn_hits: bool = True,
    ) -> float:
        """
        Compute a combined relevance score for a single track from the
        semantic (CLAP KNN) leg and the predicted-tag components.

        Weights are dynamically normalised based on which inputs actually
        contributed, so scores always use the full 0–1 range regardless of
        CLAP availability. Literal/text matching is handled separately by
        the BEST MATCH path and never enters this blend.

        ``cfg.tags.enabled`` gates the entire tag pipeline — both
        prediction (handled elsewhere) and search-time scoring. When
        False, ``tracks.tags_predicted`` is ignored even if previous
        runs populated it. This makes the toggle a single switch for
        "use tags" as a user expects, not a misleading "stop predicting
        but keep using" semi-state.
        """
        cfg = self.config.searcher
        tags_active = cfg.tags.enabled

        if tags_active:
            genre_score, mood_score, dance_score = self._compute_tag_components(
                parsed, track_tags
            )
        else:
            genre_score = mood_score = dance_score = 0.0

        # Build weighted sum only from active components
        components: list[tuple[float, float]] = []
        if has_knn_hits:
            components.append((cfg.weight_knn, knn_norm))
        if tags_active:
            if parsed.genres:
                components.append((cfg.weight_genre, genre_score))
            if parsed.mood_clusters:
                components.append((cfg.weight_mood, mood_score))
            if (
                parsed.min_danceability is not None
                or parsed.max_danceability is not None
            ):
                components.append((cfg.weight_danceability, dance_score))

        total_weight = sum(w for w, _ in components) or 1.0
        score = sum(w * v for w, v in components) / total_weight
        return score

    def _log_tag_contribution_summary(
        self,
        kind: str,
        parsed: ParsedQuery,
        top_track_ids: list[str],
        tags_map: dict[str, dict],
        tag_fallback_used: bool = False,
    ) -> None:
        """One-line per-query summary of how much the tag pipeline
        actually changed the top-N ranking.

        ``tags_active`` reflects the current value of
        ``searcher.tags.enabled``. When False, tag components are
        forced to 0 in scoring — the in_top counts shown here are then
        *hypothetical* (what the matches would be if tags were on),
        which is the useful A/B signal: same query, both states, compare.
        """
        tags_active = self.config.searcher.tags.enabled

        if not parsed.has_tag_constraints:
            # No tag constraints on the query — tags can't have shifted
            # ranking by definition. Keeping the summary terse.
            logger.info(
                "%s summary: q=%r no_tag_constraints top=%d tags_active=%s "
                "tag_fallback=%s",
                kind,
                parsed.raw[:60],
                len(top_track_ids),
                tags_active,
                tag_fallback_used,
            )
            return

        genre_hits = mood_hits = dance_hits = 0
        for tid in top_track_ids:
            g, m, d = self._compute_tag_components(parsed, tags_map.get(tid))
            if g > 0:
                genre_hits += 1
            if m > 0:
                mood_hits += 1
            if d > 0:
                dance_hits += 1

        top_n = len(top_track_ids)
        label = "in_top" if tags_active else "in_top_hypothetical"
        logger.info(
            "%s summary: q=%r parsed{genres=%s moods=%s dance=[%s,%s] similar=%s} "
            "top=%d tags_active=%s tag_fallback=%s %s: "
            "genre=%d/%d mood=%d/%d dance=%d/%d",
            kind,
            parsed.raw[:60],
            parsed.genres or "-",
            parsed.mood_clusters or "-",
            parsed.min_danceability if parsed.min_danceability is not None else "-",
            parsed.max_danceability if parsed.max_danceability is not None else "-",
            parsed.similar_to_track_id or "-",
            top_n,
            tags_active,
            tag_fallback_used,
            label,
            genre_hits,
            top_n,
            mood_hits,
            top_n,
            dance_hits,
            top_n,
        )

    def _derive_album_artist_results(
        self, scored_tracks: list[tuple[float, str]], limit: int
    ) -> tuple[list[str], list[str]]:
        """Derive album and artist top-N from scored track results."""
        album_scores: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        artist_scores: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))

        for score, tid in scored_tracks:
            meta = self._track_meta_cache.get(tid)
            if not meta:
                continue
            aid = meta.get("album_id")
            arid = meta.get("artist_id")
            if aid:
                cnt, best = album_scores[aid]
                album_scores[aid] = (cnt + 1, max(best, score))
            if arid:
                cnt, best = artist_scores[arid]
                artist_scores[arid] = (cnt + 1, max(best, score))

        def _sort_key(item):
            return (-item[1][0], -item[1][1])

        album_ids = [k for k, _ in sorted(album_scores.items(), key=_sort_key)][:limit]
        artist_ids = [k for k, _ in sorted(artist_scores.items(), key=_sort_key)][
            :limit
        ]
        return album_ids, artist_ids

    # ------------------------------------------------------------------
    # Search dispatch
    # ------------------------------------------------------------------

    async def _do_search(self, query: str, limit: int) -> dict:
        """Handle one search request end-to-end.

        Two independent legs run in parallel and are returned side by side,
        not merged:

          * BEST MATCH — literal/navigational FTS over artist/album/track
            names (``assemble_best_match``). Surfaced as its own top section.
          * Semantic — CLAP KNN audio neighbours, tag re-ranked. Drives the
            AI suggestion sections (tracks/albums/artists).

        FTS is no longer blended into the semantic ranking; the AI sections
        are purely semantic.
        """
        cfg = self.config.searcher
        self._track_meta_cache: dict[str, dict] = {}

        parsed = parse_query(query)
        logger.info(
            "_do_search: query=%r text_query=%r genres=%r mood_clusters=%r similar=%s",
            query,
            parsed.text_query,
            parsed.genres,
            parsed.mood_clusters,
            parsed.similar_to_track_id,
        )

        if parsed.is_similar_query and parsed.similar_to_track_id:
            return await self._do_similar_search(parsed, limit)

        # Encode the query once (CLAP text via IPC); the blob is reused by the
        # KNN leg and the mood NN fallback so we don't double the IPC round-trip.
        query_blob = await self._encode_query_blob(query)

        # --- Run the BEST MATCH (FTS) and semantic (KNN) legs in parallel ---
        best_match_coro = self._best_match_leg(parsed)
        knn_coro = self._knn_leg(query, cfg.knn_candidate_limit, query_blob)
        best_match, knn_hits = await asyncio.gather(best_match_coro, knn_coro)

        logger.info(
            "_do_search: BEST MATCH=%d, KNN=%d hits",
            len(best_match),
            len(knn_hits),
        )

        # Semantic tag fallback — only when the KNN leg returned nothing AND
        # the tag pipeline is active. Ranks purely by genre-tag overlap. (FTS
        # no longer participates; literal matches surface via BEST MATCH.)
        tag_fallback_used = False
        has_knn = bool(knn_hits)
        if (
            not has_knn
            and cfg.tags.enabled
            and parsed.has_tag_constraints
            and parsed.genres
        ):
            tag_track_ids = await self.db.get_similar_tracks_by_tags(
                parsed.genres, cfg.fts_candidate_limit
            )
            logger.info(
                "_do_search: tag fallback returned %d tracks", len(tag_track_ids)
            )
            knn_hits = [{"track_id": tid, "distance": 0.0} for tid in tag_track_ids]
            tag_fallback_used = bool(tag_track_ids)

        if not knn_hits:
            # No semantic results; BEST MATCH may still be non-empty.
            logger.info("_do_search: no semantic hits")
            self._log_tag_contribution_summary(
                "_do_search", parsed, [], {}, tag_fallback_used=False
            )
            return {"tracks": [], "albums": [], "artists": [], "best_match": best_match}

        # KNN score map (normalised 0-1, higher is better). Skipped on the
        # tag-fallback path, where distances are synthetic and uniform.
        knn_map: dict[str, float] = {}
        if has_knn:
            dists = [h["distance"] for h in knn_hits]
            min_dist = min(dists)
            max_dist = max(dists)
            dist_range = max_dist - min_dist if max_dist != min_dist else 1.0
            for h in knn_hits:
                knn_map[h["track_id"]] = 1.0 - (h["distance"] - min_dist) / dist_range

        all_track_ids = list(dict.fromkeys(h["track_id"] for h in knn_hits))

        # --- Mood (valence/arousal) leg ---
        # Union tracks closest to the query's target (V,A) with the CLAP
        # candidates, so a pure-mood query isn't limited to CLAP's (near-random)
        # neighbours. Blend weight scales with the query's mood confidence.
        mood_map: dict[str, float] = {}
        mood_weight = 0.0
        if cfg.mood.enabled:
            target_va, mood_conf = self._query_to_va(query, query_blob)
            if target_va is not None and mood_conf > 0.0:
                mood_weight = cfg.mood.weight * mood_conf
                mood_hits = await self.db.knn_search_mood(
                    target_va[0], target_va[1], cfg.mood.candidates
                )
                all_track_ids.extend(h["track_id"] for h in mood_hits)
                all_track_ids = list(dict.fromkeys(all_track_ids))
                va_bulk = await self.db.get_tracks_va_bulk(all_track_ids)
                if va_bulk:
                    d = {tid: math.dist(target_va, va)
                         for tid, va in va_bulk.items()}
                    dmin, dmax = min(d.values()), max(d.values())
                    drange = (dmax - dmin) or 1.0
                    mood_map = {tid: 1.0 - (dist - dmin) / drange
                                for tid, dist in d.items()}
                logger.info(
                    "_do_search: mood target V=%.2f A=%.2f conf=%.2f (+%d cand)",
                    target_va[0], target_va[1], mood_conf, len(mood_hits),
                )

        tags_map = await self.db.get_tracks_tags_bulk(all_track_ids)
        self._track_meta_cache = await self.db.get_tracks_album_artist_bulk(
            all_track_ids
        )

        scored: list[tuple[float, str]] = []
        for tid in all_track_ids:
            knn_norm = knn_map.get(tid, 0.0)
            track_tags = tags_map.get(tid)
            score = self._score_track(
                parsed,
                knn_norm,
                track_tags,
                has_knn_hits=has_knn,
            )
            if mood_weight > 0.0:
                # Adaptive blend: final = (1 - w)*clap + w*mood, w = weight*conf.
                score = (1.0 - mood_weight) * score + mood_weight * mood_map.get(tid, 0.0)
            scored.append((score, tid))

        scored.sort(key=lambda st: -st[0])
        top_tracks = scored[:limit]
        track_result = [tid for _, tid in top_tracks]

        album_ids, artist_ids = self._derive_album_artist_results(top_tracks, limit)

        self._log_tag_contribution_summary(
            "_do_search",
            parsed,
            track_result,
            tags_map,
            tag_fallback_used=tag_fallback_used,
        )

        return {
            "tracks": track_result,
            "albums": album_ids,
            "artists": artist_ids,
            "best_match": best_match,
        }

    async def _best_match_leg(self, parsed: ParsedQuery) -> list[dict]:
        """BEST MATCH leg — literal/navigational FTS over entity names.

        Builds artist/album/track candidates from FTS recall and runs the
        pure ``assemble_best_match`` algorithm (score, cut off, truncate,
        collapse album/artist redundancies). Returns an ordered list of
        ``{"id", "type"}`` dicts, highest rapidfuzz score first.

        Scored against the raw query, not the genre/mood-stripped text
        query: BEST MATCH answers "take me to the thing I named", which is
        a property of the whole typed string.
        """
        if not parsed.text_query:
            return []
        cfg = self.config.searcher
        rows = await self.db.search_entity_candidates(
            parsed.text_query, cfg.fts_candidate_limit
        )
        if not rows:
            return []
        candidates = [
            Entity(
                id=r["id"],
                type=r["type"],
                name=r["name"],
                album_id=r.get("album_id"),
                artist_id=r.get("artist_id"),
            )
            for r in rows
        ]
        best = assemble_best_match(
            candidates,
            parsed.raw,
            cutoff=cfg.best_match_min_fuzz_score,
            max_results=cfg.best_match_max_results,
        )
        return [{"id": e.id, "type": e.type} for e in best]

    async def _knn_leg(
        self, query: str, candidate_limit: int, blob: Optional[bytes] = None
    ) -> list[dict]:
        """CLAP KNN search leg — returns [{track_id, distance}].

        KNN-searches the audio-side index (``vec_tracks_clap``) with the
        precomputed query embedding ``blob``. CLAP is contrastively trained
        text↔audio, so text query → audio embedding is the canonical retrieval
        direction and outperforms text↔text on every semantic category in our
        benchmark.

        ``blob`` is encoded once by the caller (``_encode_query_blob``) and
        shared with the mood NN fallback; when None (non-ASCII query, no CLAP),
        this leg is empty and FTS handles the query.
        """
        if not self.db._vec_available:
            return []
        if blob is None:
            return []
        t0 = time.monotonic()
        results = await self.db.knn_search_audio(blob, candidate_limit)
        logger.info(
            "KNN audio search: %d results in %.3fs", len(results), time.monotonic() - t0
        )
        return results

    async def _encode_query_blob(self, query: str) -> Optional[bytes]:
        """Encode the query once (CLAP text via IPC), with the ASCII/queue
        guards the KNN and mood legs share. Non-ASCII -> None (CLAP's English
        tokenizer noises on Cyrillic/CJK)."""
        if not query.isascii():
            logger.info("CLAP encode skipped: non-ASCII query %r", query)
            return None
        if (
            self._text_encode_request_queue is None
            or self._text_encode_response_queue is None
        ):
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._encode_query_text, query)

    async def _do_similar_search(self, parsed: ParsedQuery, limit: int) -> dict:
        """Handle "songs like this" queries using CLAP audio KNN + tag matching.

        The synth query is built from the reference track's predicted
        tags, so the feature is hard-gated on ``searcher.tags.enabled``.
        When tags are off the function returns empty rather than
        falling through to a CLAP-only similar path — keeping the
        toggle as a single switch for the whole tag pipeline.

        "Songs like this" has no text query, so there is no BEST MATCH
        block — the result always carries an empty ``best_match``.
        """
        empty: dict = {"tracks": [], "albums": [], "artists": [], "best_match": []}

        if not self.config.searcher.tags.enabled:
            logger.info(
                "_do_similar_search summary: tags.enabled=False — "
                "'songs like this' requires the tag pipeline; returning empty"
            )
            return empty

        ref_id = parsed.similar_to_track_id
        if not ref_id:
            return empty

        ref_tags = await self.db.get_track_tags(ref_id)
        if not ref_tags:
            logger.info(
                "_do_similar_search summary: ref=%s no_tags_predicted — "
                "feature requires tag prediction (essentia-tensorflow); "
                "returning empty",
                ref_id,
            )
            return empty

        ref_genres = [
            g.get("label", "").lower()
            for g in (ref_tags.get("genres") or [])
            if isinstance(g, dict)
        ]
        ref_mood = ref_tags.get("mood_cluster")
        ref_dance = ref_tags.get("danceability")

        synth = ParsedQuery(
            raw=parsed.raw,
            text_query="",
            genres=ref_genres[:5],
            mood_clusters=[ref_mood] if ref_mood is not None else [],
            min_danceability=(ref_dance - 0.2) if ref_dance is not None else None,
            max_danceability=(ref_dance + 0.2) if ref_dance is not None else None,
        )

        candidate_limit = self.config.searcher.knn_candidate_limit

        # -- tag-based candidates -------------------------------------------
        tag_ids = await self.db.get_similar_tracks_by_tags(
            synth.genres, candidate_limit
        )

        # -- CLAP audio→audio KNN candidates --------------------------------
        knn_map: dict[str, float] = {}
        ref_blob = await self.db.get_track_clap_embedding(ref_id)
        if ref_blob is not None:
            knn_rows = await self.db.knn_search_audio(ref_blob, candidate_limit)
            if knn_rows:
                max_dist = max(r["distance"] for r in knn_rows) or 1.0
                for r in knn_rows:
                    knn_map[r["track_id"]] = 1.0 - r["distance"] / max_dist

        # -- merge candidate sets -------------------------------------------
        all_ids = list(
            dict.fromkeys(tid for tid in (*tag_ids, *knn_map.keys()) if tid != ref_id)
        )
        if not all_ids:
            logger.info(
                "_do_similar_search summary: ref=%s tag_candidates=%d "
                "knn_candidates=%d merged=0 — returning empty",
                ref_id,
                len(tag_ids),
                len(knn_map),
            )
            return empty

        tags_map = await self.db.get_tracks_tags_bulk(all_ids)
        self._track_meta_cache = await self.db.get_tracks_album_artist_bulk(all_ids)

        scored: list[tuple[float, str]] = []
        for tid in all_ids:
            track_tags = tags_map.get(tid)
            knn_norm = knn_map.get(tid, 0.0)
            score = self._score_track(
                synth,
                knn_norm,
                track_tags,
                has_knn_hits=bool(knn_map),
            )
            scored.append((score, tid))

        scored.sort(key=lambda x: -x[0])
        top_tracks = scored[:limit]
        track_result = [tid for _, tid in top_tracks]

        album_ids, artist_ids = self._derive_album_artist_results(top_tracks, limit)

        # Similar-search has a richer pre-merge picture than _do_search:
        # call the shared summary for the in-top tag contribution, then
        # log the candidate-set breakdown separately so the user can see
        # whether the tag leg or the CLAP-KNN leg sourced more of the
        # final ranking.
        self._log_tag_contribution_summary(
            "_do_similar_search",
            synth,
            track_result,
            tags_map,
            tag_fallback_used=False,
        )
        logger.info(
            "_do_similar_search candidates: ref=%s tag_candidates=%d "
            "knn_candidates=%d merged=%d top=%d",
            ref_id,
            len(tag_ids),
            len(knn_map),
            len(all_ids),
            len(top_tracks),
        )

        return {
            "tracks": track_result,
            "albums": album_ids,
            "artists": artist_ids,
            "best_match": [],
        }

    # ------------------------------------------------------------------
    # Search handler (IPC)
    # ------------------------------------------------------------------

    async def _run_search_handler(
        self,
        search_request_queue: multiprocessing.Queue,
        search_response_queue: multiprocessing.Queue,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Poll the request queue and dispatch searches."""
        loop = asyncio.get_running_loop()
        while not shutdown_event.is_set():
            try:
                req = await loop.run_in_executor(
                    None, lambda: search_request_queue.get(timeout=1.0)
                )
            except queue.Empty:
                continue
            except Exception:
                await asyncio.sleep(0.1)
                continue

            try:
                t0 = time.monotonic()
                result = await self._do_search(req["query"], req.get("limit", 20))
                logger.info(
                    "Search '%s': %d best-match, %d tracks, %d albums, "
                    "%d artists in %.3fs",
                    req["query"],
                    len(result.get("best_match", [])),
                    len(result.get("tracks", [])),
                    len(result.get("albums", [])),
                    len(result.get("artists", [])),
                    time.monotonic() - t0,
                )
            except Exception as e:
                logger.error("Search handler error: %s", e, exc_info=True)
                result = {"tracks": [], "albums": [], "artists": [], "best_match": []}

            search_response_queue.put(result)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self,
        shutdown_event: asyncio.Event,
        search_request_queue: multiprocessing.Queue,
        search_response_queue: multiprocessing.Queue,
        nudge_queue: Optional[multiprocessing.Queue] = None,
        embedder_nudge_queue: Optional[multiprocessing.Queue] = None,
    ):
        cfg = self.config.searcher
        poll = cfg.poll_interval_seconds
        idle_timeout = cfg.model_idle_timeout_seconds

        logger.info("SearchWorker started (tags + FTS5 + ranking)")

        await self.db._check_vec_available()

        # Recover stale tag jobs from a prior crashed session
        await self.db.recover_stale_jobs()

        # Start the search handler immediately so queries are served
        # even while tag processing / indexing is in progress.
        search_task = asyncio.create_task(
            self._run_search_handler(
                search_request_queue, search_response_queue, shutdown_event
            )
        )

        logger.info("Searcher ready (poll=%ds, idle_timeout=%ds)", poll, idle_timeout)

        # Wait for the first nudge or poll before doing heavy work
        await sleep_interruptible(poll, shutdown_event, nudge_queue, "Searcher")

        last_work_time = time.monotonic()

        while not shutdown_event.is_set():
            # Schedule new tag jobs for enriched tracks
            if cfg.tags.enabled and cfg.tags.current_version > 0:
                await self.db.schedule_new_tag_jobs(cfg.tags.current_version)

            did_work = False
            retry_gap = poll

            # Process unified tag jobs (single audio decode per track)
            if cfg.tags.enabled and cfg.tags.current_version > 0:
                while await self.db.has_pending_jobs("tags"):
                    if time.monotonic() - self._tags_load_attempted_at >= retry_gap:
                        self._load_tag_models()

                    try:
                        batch_processed = await self._process_tags_batch()
                        if batch_processed:
                            did_work = True
                            last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception(
                            "Unexpected error in tag batch processing; will retry"
                        )
                        break

            # After tag processing, nudge the embedder so it can schedule
            # CLAP jobs for tracks that now have completed tags.
            if did_work and embedder_nudge_queue is not None:
                try:
                    embedder_nudge_queue.put_nowait("tags_done")
                    logger.info("Nudged embedder after tag completion")
                except queue.Full:
                    pass

            # FTS indexing cycle (invalidate stale, reconcile, index new)
            await self._index_cycle()

            if did_work:
                continue

            # No work: check idle timeout, then sleep
            if idle_timeout > 0 and (time.monotonic() - last_work_time >= idle_timeout):
                self._unload_models()
                last_work_time = time.monotonic()

            logger.debug("No pending searcher work; sleeping %ds", poll)
            await sleep_interruptible(poll, shutdown_event, nudge_queue, "Searcher")

        # Clean shutdown
        search_task.cancel()
        try:
            await search_task
        except asyncio.CancelledError:
            pass

        logger.info("SearchWorker shutting down")

    async def _index_cycle(self) -> None:
        """Run one full FTS indexing cycle: invalidate stale, reconcile, index new."""
        try:
            await self.db.invalidate_stale_indexes()
            await self.db.reconcile_deleted_tracks()

            total = 0
            while True:
                indexed = await self.db.index_batch()
                if indexed == 0:
                    break
                total += indexed

            if total:
                logger.info("FTS indexing cycle complete: %d tracks indexed", total)
        except Exception:
            logger.exception("Error during FTS indexing cycle")


# ---------------------------------------------------------------------------
# Process entry points
# ---------------------------------------------------------------------------

_shutdown_event: Optional[asyncio.Event] = None


async def async_main(
    config: LocalFilesConfig,
    search_request_queue: multiprocessing.Queue,
    search_response_queue: multiprocessing.Queue,
    nudge_queue: Optional[multiprocessing.Queue] = None,
    embedder_nudge_queue: Optional[multiprocessing.Queue] = None,
    text_encode_request_queue: Optional[multiprocessing.Queue] = None,
    text_encode_response_queue: Optional[multiprocessing.Queue] = None,
) -> None:
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    db = AsyncSearcherDb(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_event.set)

    worker = SearchWorker(
        config, db, text_encode_request_queue, text_encode_response_queue
    )
    try:
        await worker.run(
            _shutdown_event,
            search_request_queue,
            search_response_queue,
            nudge_queue,
            embedder_nudge_queue,
        )
    except asyncio.CancelledError:
        logger.info("Searcher task cancelled")
    except Exception:
        logger.exception("Searcher crashed")


def main(
    config: LocalFilesConfig,
    logger_queue: multiprocessing.Queue,
    search_request_queue: multiprocessing.Queue,
    search_response_queue: multiprocessing.Queue,
    nudge_queue: Optional[multiprocessing.Queue] = None,
    embedder_nudge_queue: Optional[multiprocessing.Queue] = None,
    text_encode_request_queue: Optional[multiprocessing.Queue] = None,
    text_encode_response_queue: Optional[multiprocessing.Queue] = None,
) -> None:
    """Entry point for the searcher subprocess."""
    set_proc_title("kal-searcher")

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)
    root.addHandler(logging.handlers.QueueHandler(logger_queue))

    asyncio.run(
        async_main(
            config,
            search_request_queue,
            search_response_queue,
            nudge_queue,
            embedder_nudge_queue,
            text_encode_request_queue,
            text_encode_response_queue,
        )
    )

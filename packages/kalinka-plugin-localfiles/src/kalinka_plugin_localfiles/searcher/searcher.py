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
import importlib
import importlib.util
import json
import logging
import logging.handlers
import multiprocessing
import os
import queue
import signal
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from typing import Optional

from ..config_model import LocalFilesConfig
from .query_parser import ParsedQuery, parse_query
from .searcher_db import AsyncSearcherDb

logger = logging.getLogger(__name__.split(".")[-1])


# ---------------------------------------------------------------------------
# On-demand pip install
# ---------------------------------------------------------------------------

_PIP_SPECS: dict[str, str] = {
    "essentia": "essentia-tensorflow",
    "essentia_tensorflow": "essentia-tensorflow",
    "numpy": "numpy",
}

_IMPORT_NAME_ALIASES: dict[str, str] = {
    "essentia_tensorflow": "essentia",
}

_install_failed: set[str] = set()


def _resolve_probe_name(import_name: str) -> str:
    return _IMPORT_NAME_ALIASES.get(import_name, import_name)


def _is_import_available(import_name: str) -> bool:
    return importlib.util.find_spec(_resolve_probe_name(import_name)) is not None


def _ensure_package(import_name: str) -> bool:
    probe_name = _resolve_probe_name(import_name)
    if _is_import_available(import_name):
        return True
    if import_name in _install_failed:
        return False
    pip_spec = _PIP_SPECS.get(import_name, import_name)
    logger.info("Installing missing dependency '%s' via pip …", pip_spec)
    t0 = time.monotonic()
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_spec],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(
            "pip install %s failed (%.1fs) — will not retry this session:\n%s",
            pip_spec,
            time.monotonic() - t0,
            e.stderr.strip(),
        )
        _install_failed.add(import_name)
        return False
    importlib.invalidate_caches()
    available = _is_import_available(import_name)
    if not available:
        logger.error("'%s' still not importable after pip install", probe_name)
        _install_failed.add(import_name)
    else:
        logger.info(
            "pip install %s completed in %.1fs", pip_spec, time.monotonic() - t0
        )
    return available


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

    # ------------------------------------------------------------------
    # Model loading / unloading
    # ------------------------------------------------------------------

    def _load_genre_model(self):
        """Load EffNet-based genre classifier."""
        if self._effnet is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.searcher
        if not cfg.tags.enabled or cfg.tags.current_version == 0:
            return
        if not _ensure_numpy():
            return
        if not _ensure_package("essentia"):
            logger.warning("essentia-tensorflow unavailable; genre prediction disabled")
            return
        try:
            model_dir = cfg.model_dir
            effnet_path = _ensure_model_file("effnet", cfg.tags.effnet_path, model_dir)
            genre_path = _ensure_model_file("genre", cfg.tags.genre_path, model_dir)
            if effnet_path is None or genre_path is None:
                logger.warning(
                    "Genre model files unavailable; genre prediction disabled"
                )
                return
            import essentia.standard as es

            self._effnet = es.TensorflowPredictEffnetDiscogs(
                graphFilename=effnet_path, output="PartitionedCall:1"
            )
            self._genre_cls = es.TensorflowPredict2D(
                graphFilename=genre_path,
                input="serving_default_model_Placeholder",
                output="PartitionedCall:0",
            )
            logger.info("Genre model (EffNet) loaded")
        except Exception as e:
            logger.warning("Genre model loading failed: %s", e)

    def _load_mood_model(self):
        """Load VGGish-based mood classifier."""
        if self._mood_cls is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.searcher
        if not cfg.tags.enabled or cfg.tags.current_version == 0:
            return
        if not _ensure_numpy():
            return
        if not _ensure_package("essentia"):
            logger.warning("essentia-tensorflow unavailable; mood prediction disabled")
            return
        try:
            model_dir = cfg.model_dir
            vggish_path = _ensure_model_file("vggish", cfg.tags.vggish_path, model_dir)
            mood_path = _ensure_model_file(
                "mood_mirex", cfg.tags.mood_mirex_path, model_dir
            )
            if vggish_path is None or mood_path is None:
                logger.warning("Mood model files unavailable; mood prediction disabled")
                return
            import essentia.standard as es

            if self._vggish is None:
                self._vggish = es.TensorflowPredictVGGish(
                    graphFilename=vggish_path, output="model/vggish/embeddings"
                )
            self._mood_cls = es.TensorflowPredict2D(
                graphFilename=mood_path,
                input="serving_default_model_Placeholder",
                output="PartitionedCall",
            )
            logger.info("Mood model (VGGish) loaded")
        except Exception as e:
            logger.warning("Mood model loading failed: %s", e)

    def _load_danceability_model(self):
        """Load VGGish-based danceability classifier."""
        if self._dance_cls is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.searcher
        if not cfg.tags.enabled or cfg.tags.current_version == 0:
            return
        if not _ensure_numpy():
            return
        if not _ensure_package("essentia"):
            logger.warning(
                "essentia-tensorflow unavailable; danceability prediction disabled"
            )
            return
        try:
            model_dir = cfg.model_dir
            vggish_path = _ensure_model_file("vggish", cfg.tags.vggish_path, model_dir)
            dance_path = _ensure_model_file(
                "danceability", cfg.tags.danceability_path, model_dir
            )
            if vggish_path is None or dance_path is None:
                logger.warning(
                    "Danceability model files unavailable; danceability prediction disabled"
                )
                return
            import essentia.standard as es

            if self._vggish is None:
                self._vggish = es.TensorflowPredictVGGish(
                    graphFilename=vggish_path, output="model/vggish/embeddings"
                )
            self._dance_cls = es.TensorflowPredict2D(
                graphFilename=dance_path,
                input="model/Placeholder",
                output="model/Softmax",
            )
            logger.info("Danceability model (VGGish) loaded")
        except Exception as e:
            logger.warning("Danceability model loading failed: %s", e)

    def _unload_models(self):
        self._effnet = None
        self._genre_cls = None
        self._vggish = None
        self._mood_cls = None
        self._dance_cls = None
        self._tags_load_attempted_at = 0.0
        gc.collect()
        logger.info("Tag models unloaded after idle timeout")

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

    def _unload_genre_model(self):
        if self._effnet is None:
            return
        self._effnet = None
        self._genre_cls = None
        gc.collect()
        logger.info("Genre model (EffNet) unloaded")

    def _unload_mood_model(self):
        if self._mood_cls is None:
            return
        self._vggish = None
        self._mood_cls = None
        gc.collect()
        logger.info("Mood model (VGGish) unloaded")

    def _unload_danceability_model(self):
        if self._dance_cls is None:
            return
        self._dance_cls = None
        gc.collect()
        logger.info("Danceability model unloaded")

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _predict_genre(self, file_path: str) -> Optional[list]:
        """Predict genre using EffNet. Returns list of {label, score} dicts or None."""
        if self._effnet is None or self._genre_cls is None:
            return None
        try:
            import essentia.standard as es

            t0 = time.monotonic()
            loader = es.MonoLoader(filename=file_path, sampleRate=16000)
            audio = loader()
            effnet_embeddings = self._effnet(audio)
            genre_activations = self._genre_cls(effnet_embeddings)
            genre_mean = genre_activations.mean(axis=0)

            cfg = self.config.searcher.tags
            top_genres = []
            try:
                sorted_indices = genre_mean.argsort()[::-1]
                for idx in sorted_indices:
                    score = float(genre_mean[idx])
                    if score < cfg.min_confidence:
                        break
                    if len(top_genres) >= cfg.top_genres:
                        break
                    top_genres.append(
                        {"label": f"discogs_{idx}", "score": round(score, 3)}
                    )
            except Exception as e:
                logger.debug("Genre extraction error: %s", e)

            logger.info("Genre inference: %.3fs", time.monotonic() - t0)
            return top_genres
        except Exception as e:
            logger.warning("Genre prediction failed for %s: %s", file_path, e)
            return None

    def _predict_mood(self, file_path: str) -> Optional[int]:
        """Predict mood using VGGish. Returns mood cluster (0-4) or None."""
        if self._vggish is None or self._mood_cls is None:
            return None
        try:
            import essentia.standard as es

            t0 = time.monotonic()
            loader = es.MonoLoader(filename=file_path, sampleRate=16000)
            audio = loader()
            vggish_embeddings = self._vggish(audio)
            mood_activations = self._mood_cls(vggish_embeddings).mean(axis=0)
            mood_cluster = int(mood_activations.argmax())
            logger.info("Mood inference: %.3fs", time.monotonic() - t0)
            return mood_cluster
        except Exception as e:
            logger.warning("Mood prediction failed for %s: %s", file_path, e)
            return None

    def _predict_danceability(self, file_path: str) -> Optional[float]:
        """Predict danceability using VGGish. Returns score 0–1 or None."""
        if self._vggish is None or self._dance_cls is None:
            return None
        try:
            import essentia.standard as es

            t0 = time.monotonic()
            loader = es.MonoLoader(filename=file_path, sampleRate=16000)
            audio = loader()
            vggish_embeddings = self._vggish(audio)
            dance_activations = self._dance_cls(vggish_embeddings).mean(axis=0)
            danceability = (
                float(dance_activations[0]) if len(dance_activations) > 0 else 0.0
            )
            logger.info("Danceability inference: %.3fs", time.monotonic() - t0)
            return round(danceability, 3)
        except Exception as e:
            logger.warning("Danceability prediction failed for %s: %s", file_path, e)
            return None

    # ------------------------------------------------------------------
    # Tag batch processors
    # ------------------------------------------------------------------

    async def _process_genre_batch(self) -> bool:
        """Process tags_genre jobs."""
        cfg = self.config.searcher
        batch = await self.db.claim_batch("tags_genre", cfg.batch_size_tags)
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

            genres = self._predict_genre(file_path)
            if genres is None:
                logger.debug("Genre model returned None for %s", track_id)
                tags_json = json.dumps({})
            else:
                tags_json = json.dumps({"genres": genres})
            try:
                await self.db.complete_tags_job(job["id"], track_id, tags_json)
                completed.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed:
            logger.info("Genre tags written for %d tracks", len(completed))
        return True

    async def _process_mood_batch(self) -> bool:
        """Process tags_mood jobs."""
        cfg = self.config.searcher
        batch = await self.db.claim_batch("tags_mood", cfg.batch_size_tags)
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

            mood = self._predict_mood(file_path)
            if mood is None:
                logger.debug("Mood model returned None for %s", track_id)
                tags_json = json.dumps({})
            else:
                tags_json = json.dumps({"mood_cluster": mood})
            try:
                await self.db.complete_tags_job(job["id"], track_id, tags_json)
                completed.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed:
            logger.info("Mood tags written for %d tracks", len(completed))
        return True

    async def _process_danceability_batch(self) -> bool:
        """Process tags_danceability jobs."""
        cfg = self.config.searcher
        batch = await self.db.claim_batch("tags_danceability", cfg.batch_size_tags)
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

            danceability = self._predict_danceability(file_path)
            if danceability is None:
                logger.debug("Danceability model returned None for %s", track_id)
                tags_json = json.dumps({})
            else:
                tags_json = json.dumps({"danceability": danceability})
            try:
                await self.db.complete_tags_job(job["id"], track_id, tags_json)
                completed.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed:
            logger.info("Danceability tags written for %d tracks", len(completed))
        return True

    # ------------------------------------------------------------------
    # Ranking helpers
    # ------------------------------------------------------------------

    def _score_track(
        self,
        parsed: ParsedQuery,
        fts_rank_norm: float,
        knn_norm: float,
        track_tags: dict | None,
    ) -> float:
        """
        Compute a combined relevance score for a single track.

        Components (all in 0–1 range):
          - fts_rank_norm: normalised FTS5 rank (1.0 = best match)
          - knn_norm: normalised CLAP KNN similarity (1.0 = closest)
          - genre_match: fraction of query genres found in track's predicted genres
          - mood_match: 1.0 if track's mood cluster is in query's mood clusters
          - dance_match: 1.0 if track's danceability falls in query's range
        """
        cfg = self.config.searcher

        genre_score = 0.0
        mood_score = 0.0
        dance_score = 0.0

        if track_tags:
            if parsed.genres:
                t_genres = track_tags.get("genres") or []
                genre_labels = " ".join(
                    g.get("label", "") for g in t_genres if isinstance(g, dict)
                ).lower()
                matched = sum(1 for qg in parsed.genres if qg in genre_labels)
                genre_score = matched / len(parsed.genres)

            if parsed.mood_clusters:
                t_mood = track_tags.get("mood_cluster")
                if t_mood is not None and t_mood in parsed.mood_clusters:
                    mood_score = 1.0

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

        if parsed.has_tag_constraints:
            score = (
                cfg.weight_fts * fts_rank_norm
                + cfg.weight_knn * knn_norm
                + cfg.weight_genre * genre_score
                + cfg.weight_mood * mood_score
                + cfg.weight_danceability * dance_score
            )
        else:
            score = cfg.weight_fts * fts_rank_norm + cfg.weight_knn * knn_norm

        return score

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
        """Handle one search request end-to-end (FTS + KNN in parallel)."""
        empty: dict = {"tracks": [], "albums": [], "artists": []}
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

        # --- Run FTS and KNN in parallel ---
        fts_coro = self._fts_leg(parsed, cfg.fts_candidate_limit)
        knn_coro = self._knn_leg(query, cfg.knn_candidate_limit)
        fts_hits, knn_hits = await asyncio.gather(fts_coro, knn_coro)

        logger.info(
            "_do_search: FTS=%d hits, KNN=%d hits",
            len(fts_hits),
            len(knn_hits),
        )

        # Tag fallback — only when both FTS and KNN returned nothing
        if not fts_hits and not knn_hits:
            if parsed.has_tag_constraints and parsed.genres:
                tag_track_ids = await self.db.get_similar_tracks_by_tags(
                    parsed.genres, cfg.fts_candidate_limit
                )
                logger.info(
                    "_do_search: tag fallback returned %d tracks", len(tag_track_ids)
                )
                fts_hits = [{"track_id": tid, "rank": -1.0} for tid in tag_track_ids]

        if not fts_hits and not knn_hits:
            logger.info("_do_search: no hits — returning empty")
            return empty

        # --- Merge candidate sets ---
        # Build FTS score map (normalised 0-1, higher is better)
        fts_map: dict[str, float] = {}
        if fts_hits:
            ranks = [h["rank"] for h in fts_hits]
            min_rank = min(ranks)
            max_rank = max(ranks)
            rank_range = max_rank - min_rank if max_rank != min_rank else 1.0
            for h in fts_hits:
                fts_map[h["track_id"]] = 1.0 - (h["rank"] - min_rank) / rank_range

        # Build KNN score map (normalised 0-1, higher is better)
        knn_map: dict[str, float] = {}
        if knn_hits:
            dists = [h["distance"] for h in knn_hits]
            min_dist = min(dists)
            max_dist = max(dists)
            dist_range = max_dist - min_dist if max_dist != min_dist else 1.0
            for h in knn_hits:
                knn_map[h["track_id"]] = 1.0 - (h["distance"] - min_dist) / dist_range

        # Union of all candidate track IDs
        all_track_ids = list(
            dict.fromkeys(
                [h["track_id"] for h in fts_hits] + [h["track_id"] for h in knn_hits]
            )
        )

        tags_map = await self.db.get_tracks_tags_bulk(all_track_ids)

        for tid in all_track_ids:
            meta = await self.db.get_track_album_artist(tid)
            if meta:
                self._track_meta_cache[tid] = meta

        scored: list[tuple[float, str]] = []
        for tid in all_track_ids:
            fts_norm = fts_map.get(tid, 0.0)
            knn_norm = knn_map.get(tid, 0.0)
            track_tags = tags_map.get(tid)
            score = self._score_track(parsed, fts_norm, knn_norm, track_tags)
            scored.append((score, tid))

        scored.sort(key=lambda x: -x[0])
        top_tracks = scored[:limit]
        track_result = [tid for _, tid in top_tracks]

        album_ids, artist_ids = self._derive_album_artist_results(top_tracks, limit)

        return {
            "tracks": track_result,
            "albums": album_ids,
            "artists": artist_ids,
        }

    async def _fts_leg(self, parsed: ParsedQuery, candidate_limit: int) -> list[dict]:
        """FTS5 search leg — returns [{track_id, rank}]."""
        if not parsed.text_query:
            return []
        return await self.db.fts_search(parsed.text_query, candidate_limit)

    async def _knn_leg(self, query: str, candidate_limit: int) -> list[dict]:
        """CLAP KNN search leg — returns [{track_id, distance}]."""
        if not self.db._vec_available:
            return []
        if (
            self._text_encode_request_queue is None
            or self._text_encode_response_queue is None
        ):
            return []
        loop = asyncio.get_running_loop()
        blob = await loop.run_in_executor(None, self._encode_query_text, query)
        if blob is None:
            return []
        t0 = time.monotonic()
        results = await self.db.knn_search_text(blob, candidate_limit)
        logger.info(
            "KNN text search: %d results in %.3fs", len(results), time.monotonic() - t0
        )
        return results

    async def _do_similar_search(self, parsed: ParsedQuery, limit: int) -> dict:
        """Handle "songs like this" queries using CLAP audio KNN + tag matching."""
        empty: dict = {"tracks": [], "albums": [], "artists": []}
        ref_id = parsed.similar_to_track_id
        if not ref_id:
            return empty

        ref_tags = await self.db.get_track_tags(ref_id)
        if not ref_tags:
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
            return empty

        tags_map = await self.db.get_tracks_tags_bulk(all_ids)
        for tid in all_ids:
            meta = await self.db.get_track_album_artist(tid)
            if meta:
                self._track_meta_cache[tid] = meta

        scored: list[tuple[float, str]] = []
        for tid in all_ids:
            track_tags = tags_map.get(tid)
            knn_norm = knn_map.get(tid, 0.0)
            score = self._score_track(synth, 0.0, knn_norm, track_tags)
            scored.append((score, tid))

        scored.sort(key=lambda x: -x[0])
        top_tracks = scored[:limit]
        track_result = [tid for _, tid in top_tracks]

        album_ids, artist_ids = self._derive_album_artist_results(top_tracks, limit)

        return {
            "tracks": track_result,
            "albums": album_ids,
            "artists": artist_ids,
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
        while not shutdown_event.is_set():
            try:
                req = search_request_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            except Exception:
                await asyncio.sleep(0.05)
                continue

            try:
                t0 = time.monotonic()
                result = await self._do_search(req["query"], req.get("limit", 20))
                logger.info(
                    "Search '%s': %d tracks, %d albums, %d artists in %.3fs",
                    req["query"],
                    len(result.get("tracks", [])),
                    len(result.get("albums", [])),
                    len(result.get("artists", [])),
                    time.monotonic() - t0,
                )
            except Exception as e:
                logger.error("Search handler error: %s", e, exc_info=True)
                result = {"tracks": [], "albums": [], "artists": []}

            search_response_queue.put(result)

    # ------------------------------------------------------------------
    # Interruptible sleep
    # ------------------------------------------------------------------

    async def _sleep_interruptible(
        self,
        duration: float,
        shutdown_event: asyncio.Event,
        nudge_queue: Optional[multiprocessing.Queue],
    ) -> bool:
        """Sleep for *duration*, waking early on shutdown or nudge.
        Returns True if woken by a nudge, False otherwise."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration
        while not shutdown_event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if nudge_queue is not None:
                try:
                    nudge_queue.get_nowait()
                    logger.info("Searcher woken by nudge")
                    return True
                except queue.Empty:
                    pass
            await asyncio.sleep(min(1.0, remaining))
        return False

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
        await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

        last_work_time = time.monotonic()

        while not shutdown_event.is_set():
            # Schedule new tag jobs for enriched tracks
            tags_config = {
                "genre_enabled": cfg.tags.enabled,
                "mood_enabled": cfg.tags.enabled,
                "danceability_enabled": cfg.tags.enabled,
            }
            await self.db.schedule_new_tag_jobs(cfg.tags.current_version, tags_config)

            did_work = False
            retry_gap = poll

            # Process tag stages sequentially with selective model loading
            tag_stages = [
                (
                    "tags_genre",
                    self._load_genre_model,
                    self._process_genre_batch,
                    self._unload_genre_model,
                ),
                (
                    "tags_mood",
                    self._load_mood_model,
                    self._process_mood_batch,
                    self._unload_mood_model,
                ),
                (
                    "tags_danceability",
                    self._load_danceability_model,
                    self._process_danceability_batch,
                    self._unload_danceability_model,
                ),
            ]

            for stage_name, load_fn, process_fn, unload_fn in tag_stages:
                if not cfg.tags.enabled or cfg.tags.current_version == 0:
                    continue

                while await self.db.has_pending_jobs(stage_name):
                    if time.monotonic() - self._tags_load_attempted_at >= retry_gap:
                        load_fn()

                    try:
                        batch_processed = await process_fn()
                        if batch_processed:
                            did_work = True
                            last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception(
                            "Unexpected error in %s batch processing; will retry",
                            stage_name,
                        )
                        break

                unload_fn()

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

            logger.info("No pending searcher work; sleeping %ds", poll)
            await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

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
    try:
        await db.init_db_search()
    except Exception:
        logger.exception("Fatal: failed to initialise search schema")
        sys.exit(1)

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

"""
Embedding worker — CLAP + Essentia pipeline.

Runs as a separate long-lived subprocess. Uses a job queue table
(embedding_jobs) for atomic claim/complete/fail semantics.

Stages:
  1. 'tags'       — Essentia-TensorFlow (EffNet + VGGish) predicts genre,
                    mood, danceability, and voice/instrumental ratio.
  2. 'clap_audio' — CLAP (laion/clap-htsat-unfused) embeds raw audio into
                    a 512-dim space shared with text queries.

After clap_audio jobs complete, album and artist embeddings are updated
by mean-pooling their tracks' CLAP vectors.

Receives natural-language search queries via search_request_queue and
responds with ranked entity IDs via search_response_queue (IPC with the
main server process which already has models loaded here).

ML dependencies are NOT listed in pyproject.toml — installed on demand.
Model files are auto-downloaded to config.embedder.model_dir if missing.
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
from typing import Optional

from ..config_model import LocalFilesConfig
from .embedder_db import AsyncEmbedderDb

logger = logging.getLogger(__name__.split(".")[-1])


# ---------------------------------------------------------------------------
# On-demand pip install
# ---------------------------------------------------------------------------

_PIP_SPECS: dict[str, str] = {
    "essentia": "essentia-tensorflow",
    "essentia_tensorflow": "essentia-tensorflow",
    "laion_clap": "laion-clap",
    "torchvision": "torchvision",
    "numpy": "numpy",
    "sqlite_vec": "sqlite-vec",
}

# Some package names differ from importable module names.
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
        logger.error("numpy unavailable; embedder cannot run")
        return False
    import numpy

    np = numpy
    return True


# ---------------------------------------------------------------------------
# Vector utilities
# ---------------------------------------------------------------------------


def encode_embedding(vector) -> bytes:
    return vector.astype(np.float32).tobytes()


def normalise(v: "np.ndarray") -> "np.ndarray":
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


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
    # 1. Use the configured path if given and it exists
    if configured_path and os.path.isfile(configured_path):
        return configured_path

    # 2. Check model_dir/<filename>
    url = _MODEL_URLS.get(name)
    if not url:
        logger.error("No download URL for model '%s'", name)
        return None

    filename = url.rsplit("/", 1)[-1]
    dest = os.path.join(model_dir, filename)
    if os.path.isfile(dest):
        return dest

    # 3. Download
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
# EmbeddingWorker
# ---------------------------------------------------------------------------


class EmbeddingWorker:
    def __init__(self, config: LocalFilesConfig, db: AsyncEmbedderDb):
        self.config = config
        self.db = db
        # Essentia models
        self._effnet = None
        self._genre_cls = None
        self._vggish = None
        self._mood_cls = None
        self._dance_cls = None
        self._tags_load_attempted_at: float = 0.0
        # CLAP model
        self._clap = None
        self._clap_available = False
        self._clap_load_attempted_at: float = 0.0

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_clap_model(self):
        if self._clap_available:
            return
        self._clap_load_attempted_at = time.monotonic()
        cfg = self.config.embedder
        if cfg.clap.current_version == 0:
            return

        if not _ensure_numpy():
            return

        # torchvision is an implicit runtime dependency of laion-clap
        for pkg in ("torchvision", "laion_clap"):
            if not _ensure_package(pkg):
                logger.warning(
                    "%s unavailable; CLAP audio embedding disabled. "
                    "Install laion-clap and torchvision manually to enable semantic search.",
                    pkg,
                )
                return

        try:
            import laion_clap

            self._clap = laion_clap.CLAP_Module(enable_fusion=False)
            if cfg.clap.ckpt_path:
                # User provided an explicit local checkpoint path
                self._clap.load_ckpt(cfg.clap.ckpt_path)
                logger.info("CLAP model loaded from: %s", cfg.clap.ckpt_path)
            else:
                # Auto-download from HuggingFace (laion-clap default checkpoint)
                logger.info("Downloading CLAP checkpoint from HuggingFace …")
                self._clap.load_ckpt()
                logger.info("CLAP model loaded (%s)", cfg.clap.model_name)
            self._clap_available = True
        except Exception as e:
            logger.warning("CLAP model loading failed: %s; audio embedding disabled", e)

    def _load_genre_model(self):
        """Load EffNet-based genre classifier only."""
        if self._effnet is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.embedder
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
        """Load VGGish-based mood classifier only."""
        if self._mood_cls is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.embedder
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
        """Load VGGish-based danceability classifier only."""
        if self._dance_cls is not None:
            return
        self._tags_load_attempted_at = time.monotonic()
        cfg = self.config.embedder
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

    def _unload_genre_model(self):
        """Unload EffNet models only."""
        if self._effnet is None:
            return
        self._effnet = None
        self._genre_cls = None
        gc.collect()
        logger.info("Genre model (EffNet) unloaded")

    def _unload_mood_model(self):
        """Unload VGGish and mood classifier."""
        if self._mood_cls is None:
            return
        self._vggish = None
        self._mood_cls = None
        gc.collect()
        logger.info("Mood model (VGGish) unloaded")

    def _unload_danceability_model(self):
        """Unload danceability classifier."""
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
            # Load audio at 16kHz mono
            loader = es.MonoLoader(filename=file_path, sampleRate=16000)
            audio = loader()

            # Genre via EffNet-Discogs backbone
            effnet_embeddings = self._effnet(audio)
            genre_activations = self._genre_cls(effnet_embeddings)
            # Mean over time frames
            genre_mean = genre_activations.mean(axis=0)

            cfg = self.config.embedder.tags
            # Load genre metadata (label list is co-located with the model, but we
            # use an offline list derived from Discogs-400 for portability)
            top_genres = []
            try:
                # genre_mean has 400 classes — sort by score descending
                sorted_indices = genre_mean.argsort()[::-1]
                for idx in sorted_indices:
                    score = float(genre_mean[idx])
                    if score < cfg.min_confidence:
                        break
                    if len(top_genres) >= cfg.top_genres:
                        break
                    # We don't have the label list embedded here; store by index
                    # with score so the DB is still useful even without labels.
                    # The label can be resolved from the discogs-400 metadata file.
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
        """Predict danceability using VGGish. Returns danceability score or None."""
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

    def _compute_clap_audio(self, file_path: str) -> Optional[bytes]:
        """
        Compute 512-dim CLAP audio embedding for file_path.
        Returns raw float32 bytes or None on failure.
        """
        if not self._clap_available:
            return None
        try:
            import numpy

            t0 = time.monotonic()
            # laion-clap accepts file paths directly
            embeddings = self._clap.get_audio_embedding_from_filelist(
                [file_path], use_tensor=False
            )
            if embeddings is None or len(embeddings) == 0:
                return None
            vec = numpy.array(embeddings[0], dtype=numpy.float32)
            vec = normalise(vec)
            logger.info("CLAP audio embedding: %.3fs", time.monotonic() - t0)
            return encode_embedding(vec)
        except Exception as e:
            logger.warning("CLAP embedding failed for %s: %s", file_path, e)
            return None

    def _encode_query(self, query: str) -> Optional[bytes]:
        """Encode a text query with CLAP. Returns float32 bytes or None."""
        if not self._clap_available:
            return None
        try:
            import numpy

            embeddings = self._clap.get_text_embedding([query], use_tensor=False)
            if embeddings is None or len(embeddings) == 0:
                return None
            vec = numpy.array(embeddings[0], dtype=numpy.float32)
            vec = normalise(vec)
            return encode_embedding(vec)
        except Exception as e:
            logger.warning("CLAP text encoding failed: %s", e)
            return None

    def _compute_clap_text(
        self, title: str, artist_name: str, album_title: str
    ) -> Optional[bytes]:
        """Encode track metadata text with CLAP text encoder. Returns float32 bytes."""
        if not self._clap_available:
            return None
        parts = []
        if artist_name:
            parts.append(artist_name)
        if title:
            parts.append(title)
        text = " - ".join(parts)
        if album_title:
            text += f" ({album_title})"
        if not text.strip():
            return None
        return self._encode_query(text)

    # ------------------------------------------------------------------
    # Batch processors
    # ------------------------------------------------------------------

    async def _process_genre_batch(self) -> bool:
        """Process tags_genre jobs."""
        cfg = self.config.embedder
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
        cfg = self.config.embedder
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
        cfg = self.config.embedder
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

    async def _process_clap_batch(self) -> bool:
        cfg = self.config.embedder
        batch = await self.db.claim_batch("clap_audio", cfg.batch_size_clap)
        if not batch:
            return False

        completed_track_ids = []
        for job in batch:
            track_id = job["entity_id"]
            file_path = await self.db.get_file_path_for_track(track_id)
            if file_path is None:
                await self.db.fail_job(
                    job["id"], "track not found", cfg.max_job_attempts
                )
                continue

            blob = self._compute_clap_audio(file_path)
            if blob is None:
                await self.db.fail_job(
                    job["id"], "clap returned None", cfg.max_job_attempts
                )
                continue

            try:
                await self.db.complete_clap_job(
                    job["id"], track_id, blob, job["model_version"]
                )
                completed_track_ids.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed_track_ids:
            logger.info(
                "CLAP embeddings written for %d tracks", len(completed_track_ids)
            )
            await self._update_aggregate_embeddings(completed_track_ids)
        return True

    async def _update_aggregate_embeddings(self, track_ids: list[str]) -> None:
        """Mean-pool CLAP embeddings for affected albums and artists."""
        if not _ensure_numpy():
            return

        album_ids: set[str] = set()
        artist_ids: set[str] = set()
        for tid in track_ids:
            aid = await self.db.get_album_id_for_track(tid)
            if aid:
                album_ids.add(aid)
            arid = await self.db.get_artist_id_for_track(tid)
            if arid:
                artist_ids.add(arid)

        for album_id in album_ids:
            blobs = await self.db.get_track_embeddings_for_album(album_id)
            if not blobs:
                continue
            try:
                vecs = [np.frombuffer(b, dtype=np.float32) for b in blobs]
                mean_vec = normalise(np.mean(vecs, axis=0))
                await self.db.update_album_embedding(
                    album_id, encode_embedding(mean_vec)
                )
            except Exception as e:
                logger.warning(
                    "Album embedding aggregation failed for %s: %s", album_id, e
                )

        for artist_id in artist_ids:
            blobs = await self.db.get_track_embeddings_for_artist(artist_id)
            if not blobs:
                continue
            try:
                vecs = [np.frombuffer(b, dtype=np.float32) for b in blobs]
                mean_vec = normalise(np.mean(vecs, axis=0))
                await self.db.update_artist_embedding(
                    artist_id, encode_embedding(mean_vec)
                )
            except Exception as e:
                logger.warning(
                    "Artist embedding aggregation failed for %s: %s", artist_id, e
                )

    async def _process_clap_text_batch(self) -> bool:
        """Process clap_text jobs: embed track metadata text with CLAP."""
        cfg = self.config.embedder
        batch = await self.db.claim_batch("clap_text", cfg.batch_size_clap)
        if not batch:
            return False

        completed_track_ids = []
        for job in batch:
            track_id = job["entity_id"]
            meta = await self.db.get_track_metadata_for_embedding(track_id)
            if meta is None:
                await self.db.fail_job(
                    job["id"], "track metadata not found", cfg.max_job_attempts
                )
                continue

            blob = self._compute_clap_text(
                meta["title"], meta["artist_name"], meta["album_title"]
            )
            if blob is None:
                await self.db.fail_job(
                    job["id"], "clap text returned None", cfg.max_job_attempts
                )
                continue

            try:
                await self.db.complete_clap_text_job(
                    job["id"], track_id, blob, job["model_version"]
                )
                completed_track_ids.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed_track_ids:
            logger.info(
                "CLAP text embeddings written for %d tracks", len(completed_track_ids)
            )
            await self._update_aggregate_text_embeddings(completed_track_ids)
        return True

    async def _update_aggregate_text_embeddings(self, track_ids: list[str]) -> None:
        """Embed album/artist metadata text directly (not mean-pooled)."""
        if not self._clap_available:
            return

        album_ids: set[str] = set()
        artist_ids: set[str] = set()
        for tid in track_ids:
            aid = await self.db.get_album_id_for_track(tid)
            if aid:
                album_ids.add(aid)
            arid = await self.db.get_artist_id_for_track(tid)
            if arid:
                artist_ids.add(arid)

        for album_id in album_ids:
            meta = await self.db.get_album_metadata_for_text_embedding(album_id)
            if meta is None:
                continue
            text = meta["title"]
            if meta["artist_name"]:
                text += f" by {meta['artist_name']}"
            if not text.strip():
                continue
            blob = self._encode_query(text)
            if blob:
                try:
                    await self.db.update_album_text_embedding(album_id, blob)
                except Exception as e:
                    logger.warning(
                        "Album text embedding failed for %s: %s", album_id, e
                    )

        for artist_id in artist_ids:
            name = await self.db.get_artist_name(artist_id)
            if not name:
                continue
            blob = self._encode_query(name)
            if blob:
                try:
                    await self.db.update_artist_text_embedding(artist_id, blob)
                except Exception as e:
                    logger.warning(
                        "Artist text embedding failed for %s: %s", artist_id, e
                    )

    # ------------------------------------------------------------------
    # Search (IPC from main process)
    # ------------------------------------------------------------------

    async def _do_search(self, query: str, limit: int) -> dict:
        """Encode query with CLAP and run KNN on both audio and text vec tables,
        then merge results using Reciprocal Rank Fusion (RRF)."""
        empty: dict = {"tracks": [], "albums": [], "artists": []}
        blob = self._encode_query(query)
        if blob is None:
            return empty

        rrf_k = 60
        result: dict = {}
        for entity_type, audio_table, text_table in [
            ("tracks", "vec_tracks_clap", "vec_tracks_clap_text"),
            ("albums", "vec_albums_clap", "vec_albums_clap_text"),
            ("artists", "vec_artists_clap", "vec_artists_clap_text"),
        ]:
            audio_hits = await self.db.knn_search(audio_table, blob, limit)
            text_hits = await self.db.knn_search(text_table, blob, limit)

            scores: dict[str, float] = {}
            for rank, r in enumerate(audio_hits):
                scores[r["id"]] = scores.get(r["id"], 0) + 1 / (rrf_k + rank + 1)
            for rank, r in enumerate(text_hits):
                scores[r["id"]] = scores.get(r["id"], 0) + 1 / (rrf_k + rank + 1)

            merged_ids = sorted(scores, key=lambda x: -scores[x])[:limit]
            result[entity_type] = merged_ids
        return result

    async def _run_search_handler(
        self,
        search_request_queue: multiprocessing.Queue,
        search_response_queue: multiprocessing.Queue,
        shutdown_event: asyncio.Event,
    ) -> None:
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
                logger.error("Search handler error: %s", e)
                result = {"tracks": [], "albums": [], "artists": []}

            search_response_queue.put(result)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _sleep_interruptible(
        self,
        duration: float,
        shutdown_event: asyncio.Event,
        nudge_queue: Optional[multiprocessing.Queue],
    ) -> bool:
        """Sleep for *duration* seconds, waking early on shutdown or nudge.
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
                    logger.info("Embedder woken by enricher nudge")
                    return True
                except queue.Empty:
                    pass
            await asyncio.sleep(min(1.0, remaining))
        return False

    async def run(
        self,
        shutdown_event: asyncio.Event,
        search_request_queue: Optional[multiprocessing.Queue] = None,
        search_response_queue: Optional[multiprocessing.Queue] = None,
        nudge_queue: Optional[multiprocessing.Queue] = None,
    ):
        cfg = self.config.embedder
        poll = cfg.poll_interval_seconds
        idle_timeout = cfg.model_idle_timeout_seconds

        logger.info("EmbeddingWorker started (Essentia + CLAP pipeline)")

        # Recover stale jobs from a prior crashed session, then wait for the
        # first nudge or poll cycle before loading any models.  This prevents
        # TensorFlow / CLAP from being pulled into memory at system startup
        # when the OS is still settling.
        await self.db.recover_stale_jobs()

        search_task = None
        if search_request_queue is not None and search_response_queue is not None:
            search_task = asyncio.create_task(
                self._run_search_handler(
                    search_request_queue, search_response_queue, shutdown_event
                )
            )

        # Load CLAP eagerly so search works even when all jobs are already done.
        # Tag models are still loaded lazily (they are not needed for search).
        if cfg.clap.current_version > 0 and search_request_queue is not None:
            self._load_clap_model()

        logger.info(
            "Embedder ready (poll=%ds, idle_timeout=%ds)",
            poll,
            idle_timeout,
        )
        await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

        last_work_time = time.monotonic()

        while not shutdown_event.is_set():
            # Schedule new embedding jobs for enriched tracks
            tags_config = {
                "genre_enabled": cfg.tags.enabled,
                "mood_enabled": cfg.tags.enabled,
                "danceability_enabled": cfg.tags.enabled,
            }
            await self.db.schedule_new_jobs(
                cfg.tags.current_version, cfg.clap.current_version, tags_config
            )

            did_work = False

            # Process tag stages sequentially with selective model loading
            retry_gap = poll
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
                    # Load model if needed
                    if time.monotonic() - self._tags_load_attempted_at >= retry_gap:
                        load_fn()

                    # Process batches
                    try:
                        batch_processed = await process_fn()
                        if batch_processed:
                            did_work = True
                            last_work_time = time.monotonic()
                        else:
                            # No batch was available, break to next stage
                            break
                    except Exception:
                        logger.exception(
                            "Unexpected error in batch processing; will retry"
                        )
                        break

                # Unload model after all batches for this stage
                unload_fn()

            # Process CLAP audio after all tag stages
            if cfg.clap.current_version > 0:
                while await self.db.has_pending_jobs("clap_audio"):
                    if time.monotonic() - self._clap_load_attempted_at >= retry_gap:
                        self._load_clap_model()

                    try:
                        batch_processed = await self._process_clap_batch()
                        if batch_processed:
                            did_work = True
                            last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception("Unexpected error in CLAP batch; will retry")
                        break

            # Process CLAP text (metadata) embeddings — uses same CLAP model
            if cfg.clap.current_version > 0:
                while await self.db.has_pending_jobs("clap_text"):
                    if time.monotonic() - self._clap_load_attempted_at >= retry_gap:
                        self._load_clap_model()

                    try:
                        batch_processed = await self._process_clap_text_batch()
                        if batch_processed:
                            did_work = True
                            last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception(
                            "Unexpected error in CLAP text batch; will retry"
                        )
                        break

            if did_work:
                continue

            # --- no work done: check idle timeout and sleep ---
            if idle_timeout > 0 and (time.monotonic() - last_work_time >= idle_timeout):
                self._unload_models()
                last_work_time = time.monotonic()  # reset timer after unload

            logger.info("No pending embedding work; sleeping %ds", poll)
            await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

        if search_task is not None:
            search_task.cancel()
            try:
                await search_task
            except asyncio.CancelledError:
                pass

        logger.info("EmbeddingWorker shutting down")


# ---------------------------------------------------------------------------
# Process entry points
# ---------------------------------------------------------------------------

_shutdown_event: Optional[asyncio.Event] = None


async def async_main(
    config: LocalFilesConfig,
    search_request_queue: Optional[multiprocessing.Queue] = None,
    search_response_queue: Optional[multiprocessing.Queue] = None,
    nudge_queue: Optional[multiprocessing.Queue] = None,
):
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    db = AsyncEmbedderDb(config)
    try:
        await db.init_db_embeddings()
    except Exception:
        logger.exception("Fatal: failed to initialise embedding schema")
        sys.exit(1)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_event.set)

    worker = EmbeddingWorker(config, db)
    try:
        await worker.run(
            _shutdown_event, search_request_queue, search_response_queue, nudge_queue
        )
    except asyncio.CancelledError:
        logger.info("Embedder task cancelled")
    except Exception:
        logger.exception("Embedder crashed")


def main(
    config: LocalFilesConfig,
    logger_queue: multiprocessing.Queue,
    search_request_queue: Optional[multiprocessing.Queue] = None,
    search_response_queue: Optional[multiprocessing.Queue] = None,
    nudge_queue: Optional[multiprocessing.Queue] = None,
):
    """Entry point for the embedder subprocess."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)
    root.addHandler(logging.handlers.QueueHandler(logger_queue))

    asyncio.run(
        async_main(config, search_request_queue, search_response_queue, nudge_queue)
    )

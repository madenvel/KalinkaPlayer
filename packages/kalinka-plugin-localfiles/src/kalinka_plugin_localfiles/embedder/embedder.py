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
import importlib
import importlib.util
import json
import logging
import logging.handlers
import multiprocessing
import os
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


def decode_embedding(blob: bytes) -> "np.ndarray":
    return np.frombuffer(blob, dtype=np.float32)


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
    "mood_mirex": "https://essentia.upf.edu/models/classification-heads/mood_mirex/mood_mirex-audioset-vggish-1.pb",
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
# Discogs-400 genre normalisation
# ---------------------------------------------------------------------------


def _normalise_genre_label(raw: str) -> str:
    """'Electronic---Techno' → 'techno'"""
    return raw.rsplit("---", 1)[-1].lower().strip()


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
        self._tags_available = False
        # CLAP model
        self._clap = None
        self._clap_available = False

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_tags_models(self):
        if self._tags_available:
            return
        cfg = self.config.embedder
        if not cfg.tags.enabled or cfg.tags.current_version == 0:
            return

        if not _ensure_package("essentia"):
            logger.warning(
                "essentia-tensorflow unavailable; tag prediction disabled. "
                "Install essentia-tensorflow manually to enable genre/mood tags."
            )
            return

        try:
            import essentia.standard as es

            model_dir = cfg.model_dir
            paths = {
                "effnet": _ensure_model_file("effnet", cfg.tags.effnet_path, model_dir),
                "genre": _ensure_model_file("genre", cfg.tags.genre_path, model_dir),
                "vggish": _ensure_model_file("vggish", cfg.tags.vggish_path, model_dir),
                "mood_mirex": _ensure_model_file(
                    "mood_mirex", cfg.tags.mood_mirex_path, model_dir
                ),
                "danceability": _ensure_model_file(
                    "danceability", cfg.tags.danceability_path, model_dir
                ),
            }

            if any(p is None for p in paths.values()):
                logger.warning(
                    "One or more Essentia model files unavailable; tag prediction disabled"
                )
                return

            self._effnet = es.TensorflowPredictEffnetDiscogs(
                graphFilename=paths["effnet"], output="PartitionedCall:1"
            )
            self._genre_cls = es.TensorflowPredict2D(
                graphFilename=paths["genre"],
                input="serving_default_model_Placeholder",
                output="PartitionedCall:0",
            )
            self._vggish = es.TensorflowPredictVGGish(
                graphFilename=paths["vggish"], output="model/vggish/embeddings"
            )
            self._mood_cls = es.TensorflowPredict2D(
                graphFilename=paths["mood_mirex"],
                input="serving_default_model_Placeholder",
                output="PartitionedCall:0",
            )
            self._dance_cls = es.TensorflowPredict2D(
                graphFilename=paths["danceability"],
                input="serving_default_model_Placeholder",
                output="PartitionedCall:0",
            )
            self._tags_available = True
            logger.info("Essentia tag models loaded successfully")
        except Exception as e:
            logger.warning(
                "Essentia model loading failed: %s; tag prediction disabled", e
            )

    def _load_clap_model(self):
        if self._clap_available:
            return
        cfg = self.config.embedder
        if cfg.clap.current_version == 0:
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

    def load_models(self):
        if not _ensure_numpy():
            return
        self._load_tags_models()
        self._load_clap_model()

    def _unload_models(self):
        self._effnet = None
        self._genre_cls = None
        self._vggish = None
        self._mood_cls = None
        self._dance_cls = None
        self._tags_available = False
        self._clap = None
        self._clap_available = False
        import gc
        gc.collect()
        logger.info("Models unloaded after idle timeout")

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _predict_tags(self, file_path: str) -> Optional[dict]:
        """Run Essentia tag prediction. Returns structured dict or None."""
        if not self._tags_available:
            return None
        try:
            import essentia.standard as es

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

            # Mood + danceability via VGGish backbone
            vggish_embeddings = self._vggish(audio)
            mood_activations = self._mood_cls(vggish_embeddings).mean(axis=0)
            dance_activations = self._dance_cls(vggish_embeddings).mean(axis=0)

            mood_cluster = int(mood_activations.argmax())
            danceability = (
                float(dance_activations[0]) if len(dance_activations) > 0 else 0.0
            )

            return {
                "genres": top_genres,
                "mood_cluster": mood_cluster,
                "danceability": round(danceability, 3),
                "voice_instrumental": None,  # placeholder — future VGGish voice model
            }
        except Exception as e:
            logger.warning("Tag prediction failed for %s: %s", file_path, e)
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

            # laion-clap accepts file paths directly
            embeddings = self._clap.get_audio_embedding_from_filelist(
                [file_path], use_tensor=False
            )
            if embeddings is None or len(embeddings) == 0:
                return None
            vec = numpy.array(embeddings[0], dtype=numpy.float32)
            vec = normalise(vec)
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

    # ------------------------------------------------------------------
    # Batch processors
    # ------------------------------------------------------------------

    async def _process_tags_batch(self) -> bool:
        cfg = self.config.embedder
        batch = await self.db.claim_batch("tags", cfg.batch_size_tags)
        if not batch:
            return False

        completed = []
        for job in batch:
            track_id = job["entity_id"]
            # Fetch file_path
            async with self.db._get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
                )
                row = await cursor.fetchone()
            if not row:
                await self.db.fail_job(
                    job["id"], "track not found", cfg.max_job_attempts
                )
                continue

            tag_data = self._predict_tags(row[0])
            tags_json = json.dumps(tag_data or {})
            try:
                await self.db.complete_tags_job(job["id"], track_id, tags_json)
                completed.append(track_id)
            except Exception as e:
                await self.db.fail_job(job["id"], str(e), cfg.max_job_attempts)

        if completed:
            logger.info("Tags written for %d tracks", len(completed))
            # Unlock clap_audio jobs for the completed tracks
            await self.db.schedule_new_jobs(
                cfg.tags.current_version, cfg.clap.current_version
            )
        return True

    async def _process_clap_batch(self) -> bool:
        cfg = self.config.embedder
        batch = await self.db.claim_batch("clap_audio", cfg.batch_size_clap)
        if not batch:
            return False

        completed_track_ids = []
        for job in batch:
            track_id = job["entity_id"]
            async with self.db._get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
                )
                row = await cursor.fetchone()
            if not row:
                await self.db.fail_job(
                    job["id"], "track not found", cfg.max_job_attempts
                )
                continue

            blob = self._compute_clap_audio(row[0])
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

    # ------------------------------------------------------------------
    # Search (IPC from main process)
    # ------------------------------------------------------------------

    async def _do_search(self, query: str, limit: int) -> dict:
        """Encode query with CLAP and run KNN on all entity tables."""
        empty: dict = {"tracks": [], "albums": [], "artists": []}
        blob = self._encode_query(query)
        if blob is None:
            return empty

        result: dict = {}
        for entity_type, (vec_table, _) in [
            ("tracks", ("vec_tracks_clap", "track_id")),
            ("albums", ("vec_albums_clap", "album_id")),
            ("artists", ("vec_artists_clap", "artist_id")),
        ]:
            rows = await self.db.knn_search(vec_table, blob, limit)
            result[entity_type] = [r["id"] for r in rows]
        return result

    async def _run_search_handler(
        self,
        search_request_queue: multiprocessing.Queue,
        search_response_queue: multiprocessing.Queue,
        shutdown_event: asyncio.Event,
    ) -> None:
        import queue as _queue

        while not shutdown_event.is_set():
            try:
                req = search_request_queue.get_nowait()
            except _queue.Empty:
                await asyncio.sleep(0.05)
                continue
            except Exception:
                await asyncio.sleep(0.05)
                continue

            try:
                result = await self._do_search(req["query"], req.get("limit", 20))
            except Exception as e:
                logger.error("Search handler error: %s", e)
                result = {"tracks": [], "albums": [], "artists": []}

            search_response_queue.put(result)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self,
        shutdown_event: asyncio.Event,
        search_request_queue: Optional[multiprocessing.Queue] = None,
        search_response_queue: Optional[multiprocessing.Queue] = None,
        nudge_queue: Optional[multiprocessing.Queue] = None,
    ):
        import queue as _queue

        cfg = self.config.embedder
        poll = cfg.poll_interval_seconds
        idle_timeout = cfg.model_idle_timeout_seconds

        logger.info("EmbeddingWorker started (Essentia + CLAP pipeline)")

        # Recover stale jobs from a prior crashed session
        await self.db.recover_stale_jobs()
        # Queue any new work
        await self.db.schedule_new_jobs(
            cfg.tags.current_version, cfg.clap.current_version
        )

        search_task = None
        if search_request_queue is not None and search_response_queue is not None:
            search_task = asyncio.create_task(
                self._run_search_handler(
                    search_request_queue, search_response_queue, shutdown_event
                )
            )

        last_work_time = time.monotonic()

        while not shutdown_event.is_set():
            # Lazy model load — also handles reload after offload
            if not self._tags_available and not self._clap_available:
                self.load_models()

            did_work = False
            try:
                if self._tags_available and cfg.tags.current_version > 0:
                    did_work |= await self._process_tags_batch()
                if self._clap_available and cfg.clap.current_version > 0:
                    did_work |= await self._process_clap_batch()
            except Exception:
                logger.exception("Unexpected error in embedding batch; will retry")
                did_work = False

            if did_work:
                last_work_time = time.monotonic()
            else:
                # Periodically check for newly enriched tracks
                await self.db.schedule_new_jobs(
                    cfg.tags.current_version, cfg.clap.current_version
                )

                # Offload models after prolonged idle
                if idle_timeout > 0 and (self._tags_available or self._clap_available):
                    if time.monotonic() - last_work_time >= idle_timeout:
                        self._unload_models()

                logger.info("No pending embedding work; sleeping %ds", poll)
                # Interruptible sleep: wakes on shutdown or enricher nudge
                deadline = asyncio.get_event_loop().time() + poll
                while not shutdown_event.is_set():
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    if nudge_queue is not None:
                        try:
                            nudge_queue.get_nowait()
                            logger.info("Embedder woken by enricher nudge")
                            break
                        except _queue.Empty:
                            pass
                    await asyncio.sleep(min(1.0, remaining))

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
        await worker.run(_shutdown_event, search_request_queue, search_response_queue, nudge_queue)
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
    root.setLevel(logging.DEBUG)
    root.addHandler(logging.handlers.QueueHandler(logger_queue))

    asyncio.run(async_main(config, search_request_queue, search_response_queue, nudge_queue))

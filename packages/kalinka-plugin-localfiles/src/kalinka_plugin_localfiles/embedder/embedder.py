"""
Embedding worker — CLAP-only pipeline.

Runs as a separate long-lived subprocess. Uses a job queue table
(embedding_jobs) for atomic claim/complete/fail semantics.

Stages (handled by this process):
  - 'clap_audio' — CLAP (laion/clap-htsat-unfused) embeds raw audio into
                   a 512-dim space shared with text queries.
  - 'clap_text'  — CLAP encodes track metadata text.

Tag prediction (genre, mood, danceability) is handled by the searcher
process.  After clap_audio jobs complete, album and artist embeddings
are updated by mean-pooling their tracks' CLAP vectors.

ML dependencies are NOT listed in pyproject.toml — installed on demand.
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
from typing import Optional

from ..config_model import LocalFilesConfig
from .embedder_db import AsyncEmbedderDb

logger = logging.getLogger(__name__.split(".")[-1])


# ---------------------------------------------------------------------------
# On-demand pip install
# ---------------------------------------------------------------------------

_PIP_SPECS: dict[str, str] = {
    "laion_clap": "laion-clap",
    "torchvision": "torchvision",
    "numpy": "numpy",
    "sqlite_vec": "sqlite-vec",
}

_IMPORT_NAME_ALIASES: dict[str, str] = {}

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

from ..embedding_utils import encode_embedding, normalise


# ---------------------------------------------------------------------------
# EmbeddingWorker
# ---------------------------------------------------------------------------


class EmbeddingWorker:
    def __init__(self, config: LocalFilesConfig, db: AsyncEmbedderDb):
        self.config = config
        self.db = db
        self._clap = None
        self._clap_available = False
        self._clap_load_attempted_at: float = 0.0

    # ------------------------------------------------------------------
    # CLAP model loading
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
                self._clap.load_ckpt(cfg.clap.ckpt_path)
                logger.info("CLAP model loaded from: %s", cfg.clap.ckpt_path)
            else:
                logger.info("Downloading CLAP checkpoint from HuggingFace …")
                self._clap.load_ckpt()
                logger.info("CLAP model loaded (%s)", cfg.clap.model_name)
            self._clap_available = True
        except Exception as e:
            logger.warning("CLAP model loading failed: %s; audio embedding disabled", e)

    def _unload_clap_model(self):
        if not self._clap_available:
            return
        self._clap = None
        self._clap_available = False
        self._clap_load_attempted_at = 0.0
        gc.collect()
        logger.info("CLAP model unloaded after idle timeout")

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _compute_clap_audio(self, file_path: str) -> Optional[bytes]:
        """Compute 512-dim CLAP audio embedding for file_path."""
        if not self._clap_available:
            return None
        try:
            import numpy

            t0 = time.monotonic()
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
        """Encode track metadata text with CLAP text encoder."""
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
    # Text-encode service (IPC for searcher KNN queries)
    # ------------------------------------------------------------------

    async def _run_text_encode_handler(
        self,
        request_queue: multiprocessing.Queue,
        response_queue: multiprocessing.Queue,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Poll for text-encode requests from the searcher and return CLAP vectors."""
        loop = asyncio.get_running_loop()
        while not shutdown_event.is_set():
            try:
                req = await loop.run_in_executor(
                    None, lambda: request_queue.get(timeout=1.0)
                )
            except queue.Empty:
                continue
            except Exception:
                await asyncio.sleep(0.1)
                continue

            query = req.get("query", "")
            try:
                self._load_clap_model()
                blob = self._encode_query(query)
                self._last_work_time = time.monotonic()
                response_queue.put({"blob": blob})
            except Exception as e:
                logger.warning("Text-encode handler error: %s", e)
                response_queue.put({"blob": None})

    # ------------------------------------------------------------------
    # Interruptible sleep
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
                    logger.info("Embedder woken by nudge")
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
        nudge_queue: Optional[multiprocessing.Queue] = None,
        text_encode_request_queue: Optional[multiprocessing.Queue] = None,
        text_encode_response_queue: Optional[multiprocessing.Queue] = None,
    ):
        cfg = self.config.embedder
        poll = cfg.poll_interval_seconds
        idle_timeout = cfg.model_idle_timeout_seconds
        embedding_enabled = cfg.enabled

        logger.info("EmbeddingWorker started (CLAP-only pipeline)")

        if embedding_enabled:
            await self.db.recover_stale_jobs()

        # Start text-encode handler for searcher KNN queries (always runs)
        encode_task = None
        if text_encode_request_queue and text_encode_response_queue:
            encode_task = asyncio.create_task(
                self._run_text_encode_handler(
                    text_encode_request_queue,
                    text_encode_response_queue,
                    shutdown_event,
                )
            )
            logger.info("Text-encode handler started (serving searcher KNN queries)")

        if not embedding_enabled:
            logger.info(
                "Embedding jobs disabled; running text-encode service only"
            )
            # Just keep the text-encode handler running
            if encode_task:
                try:
                    await encode_task
                except asyncio.CancelledError:
                    pass
            else:
                # Nothing to do — wait for shutdown
                while not shutdown_event.is_set():
                    await asyncio.sleep(1.0)
            logger.info("EmbeddingWorker shutting down")
            return

        # Wait for the first nudge or poll cycle before loading models
        logger.info("Embedder ready (poll=%ds, idle_timeout=%ds)", poll, idle_timeout)
        await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

        self._last_work_time = time.monotonic()

        while not shutdown_event.is_set():
            # Schedule new CLAP jobs for tracks with completed tags
            await self.db.schedule_new_jobs(cfg.clap.current_version)

            did_work = False
            retry_gap = poll

            # Process CLAP audio
            if cfg.clap.current_version > 0:
                while await self.db.has_pending_jobs("clap_audio"):
                    if time.monotonic() - self._clap_load_attempted_at >= retry_gap:
                        self._load_clap_model()

                    try:
                        batch_processed = await self._process_clap_batch()
                        if batch_processed:
                            did_work = True
                            self._last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception("Unexpected error in CLAP batch; will retry")
                        break

            # Process CLAP text (metadata) embeddings
            if cfg.clap.current_version > 0:
                while await self.db.has_pending_jobs("clap_text"):
                    if time.monotonic() - self._clap_load_attempted_at >= retry_gap:
                        self._load_clap_model()

                    try:
                        batch_processed = await self._process_clap_text_batch()
                        if batch_processed:
                            did_work = True
                            self._last_work_time = time.monotonic()
                        else:
                            break
                    except Exception:
                        logger.exception(
                            "Unexpected error in CLAP text batch; will retry"
                        )
                        break

            if did_work:
                continue

            # No work: check idle timeout, then sleep
            if idle_timeout > 0 and (
                time.monotonic() - self._last_work_time >= idle_timeout
            ):
                self._unload_clap_model()
                self._last_work_time = time.monotonic()

            logger.info("No pending embedding work; sleeping %ds", poll)
            await self._sleep_interruptible(poll, shutdown_event, nudge_queue)

        # Clean shutdown
        if encode_task:
            encode_task.cancel()
            try:
                await encode_task
            except asyncio.CancelledError:
                pass

        logger.info("EmbeddingWorker shutting down")


# ---------------------------------------------------------------------------
# Process entry points
# ---------------------------------------------------------------------------

_shutdown_event: Optional[asyncio.Event] = None


async def async_main(
    config: LocalFilesConfig,
    nudge_queue: Optional[multiprocessing.Queue] = None,
    text_encode_request_queue: Optional[multiprocessing.Queue] = None,
    text_encode_response_queue: Optional[multiprocessing.Queue] = None,
):
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    db = AsyncEmbedderDb(config)
    if config.embedder.enabled:
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
            _shutdown_event,
            nudge_queue,
            text_encode_request_queue,
            text_encode_response_queue,
        )
    except asyncio.CancelledError:
        logger.info("Embedder task cancelled")
    except Exception:
        logger.exception("Embedder crashed")


def main(
    config: LocalFilesConfig,
    logger_queue: multiprocessing.Queue,
    nudge_queue: Optional[multiprocessing.Queue] = None,
    text_encode_request_queue: Optional[multiprocessing.Queue] = None,
    text_encode_response_queue: Optional[multiprocessing.Queue] = None,
):
    """Entry point for the embedder subprocess."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.setLevel(logging.DEBUG)
    root.addHandler(logging.handlers.QueueHandler(logger_queue))

    asyncio.run(
        async_main(
            config, nudge_queue, text_encode_request_queue, text_encode_response_queue
        )
    )

"""
Database layer for the search + tag pipeline.

Manages:
  - Tag job lifecycle (schedule / claim / complete / fail / recover) using
    the shared ``embedding_jobs`` table (tag stages only).
  - FTS5 virtual tables for full-text search.
  - Tag lookups for search re-ranking.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite

from ..config_model import LocalFilesConfig
from ..worker_utils import retry_db_locked

logger = logging.getLogger(__name__.split(".")[-1])



@retry_db_locked
class AsyncSearcherDb:
    """
    Async database layer for the search subprocess.

    All methods open short-lived connections (same pattern as AsyncEmbedderDb).
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = os.path.expanduser(config.db_path)
        self._vec_available = False

    def _get_connection(self):
        return aiosqlite.connect(self.db_path, timeout=5.0)

    @asynccontextmanager
    async def _open(self):
        async with self._get_connection() as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            yield conn

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    async def _check_vec_available(self) -> None:
        """Lazily detect whether sqlite-vec is available."""
        if self._vec_available:
            return
        try:
            import sqlite_vec

            async with self._open() as conn:
                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)

            self._vec_available = True
            logger.info("sqlite-vec loaded; KNN search enabled")
        except Exception as e:
            logger.warning("sqlite-vec not available (%s); KNN search disabled", e)

    async def _load_vec(self, conn: aiosqlite.Connection) -> None:
        """Load sqlite-vec extension into an open connection."""
        import sqlite_vec

        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)

    # ------------------------------------------------------------------
    # KNN search (CLAP vector tables)
    # ------------------------------------------------------------------

    async def knn_search_text(self, query_blob: bytes, limit: int = 50) -> list[dict]:
        """KNN search on vec_tracks_clap_text.

        Returns [{"track_id": str, "distance": float}] sorted by distance (ascending).
        """
        if not self._vec_available:
            return []
        try:
            async with self._open() as conn:
                await self._load_vec(conn)
                cursor = await conn.execute(
                    "SELECT track_id, distance FROM vec_tracks_clap_text"
                    " WHERE embedding MATCH vec_int8(?) ORDER BY distance LIMIT ?",
                    (query_blob, limit),
                )
                rows = await cursor.fetchall()
            return [{"track_id": row[0], "distance": row[1]} for row in rows]
        except Exception as e:
            logger.warning("KNN text search failed: %s", e)
            return []

    async def knn_search_audio(self, query_blob: bytes, limit: int = 50) -> list[dict]:
        """KNN search on vec_tracks_clap (audio embeddings).

        Returns [{"track_id": str, "distance": float}] sorted by distance (ascending).
        """
        if not self._vec_available:
            return []
        try:
            async with self._open() as conn:
                await self._load_vec(conn)
                cursor = await conn.execute(
                    "SELECT track_id, distance FROM vec_tracks_clap"
                    " WHERE embedding MATCH vec_int8(?) ORDER BY distance LIMIT ?",
                    (query_blob, limit),
                )
                rows = await cursor.fetchall()
            return [{"track_id": row[0], "distance": row[1]} for row in rows]
        except Exception as e:
            logger.warning("KNN audio search failed: %s", e)
            return []

    async def knn_search_mood(
        self, valence: float, arousal: float, limit: int = 200
    ) -> list[dict]:
        """Tracks closest to a target (valence, arousal), as
        [{"track_id", "distance"}] (Euclidean) ascending. Full scan, no index.
        """
        try:
            async with self._open() as conn:
                cursor = await conn.execute(
                    "SELECT id, "
                    "(mood_valence - ?) * (mood_valence - ?) + "
                    "(mood_arousal - ?) * (mood_arousal - ?) AS d2 "
                    "FROM tracks WHERE mood_valence IS NOT NULL "
                    "ORDER BY d2 LIMIT ?",
                    (valence, valence, arousal, arousal, limit),
                )
                rows = await cursor.fetchall()
            return [{"track_id": row[0], "distance": row[1] ** 0.5} for row in rows]
        except Exception as e:
            logger.warning("Mood V-A search failed: %s", e)
            return []

    async def get_tracks_va_bulk(
        self, track_ids: list[str]
    ) -> dict[str, tuple[float, float]]:
        """Return {track_id: (valence, arousal)} for tracks that have mood set."""
        if not track_ids:
            return {}
        try:
            async with self._open() as conn:
                placeholders = ",".join("?" * len(track_ids))
                cursor = await conn.execute(
                    f"SELECT id, mood_valence, mood_arousal FROM tracks "
                    f"WHERE id IN ({placeholders}) AND mood_valence IS NOT NULL",
                    track_ids,
                )
                rows = await cursor.fetchall()
            return {row[0]: (row[1], row[2]) for row in rows}
        except Exception as e:
            logger.warning("Bulk V-A fetch failed: %s", e)
            return {}

    async def get_track_clap_embedding(self, track_id: str) -> bytes | None:
        """Fetch the CLAP audio embedding for a single track."""
        if not self._vec_available:
            return None
        try:
            async with self._open() as conn:
                await self._load_vec(conn)
                cursor = await conn.execute(
                    "SELECT embedding FROM vec_tracks_clap WHERE track_id = ?",
                    (track_id,),
                )
                row = await cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning("Failed to fetch CLAP embedding for %s: %s", track_id, e)
            return None

    # ------------------------------------------------------------------
    # Tag job lifecycle
    # ------------------------------------------------------------------

    async def recover_stale_jobs(self) -> None:
        """Reset in_progress tag jobs left over from a crashed session."""
        async with self._open() as conn:
            await conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                  AND stage = 'tags'
                """
            )
            await conn.commit()
        logger.info("Stale in_progress tag jobs reset to pending")

    async def schedule_new_tag_jobs(self, tags_version: int) -> int:
        """
        Queue unified tag jobs for enriched tracks that don't have one yet.
        Returns total jobs inserted.
        """
        async with self._open() as conn:
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO embedding_jobs
                    (entity_type, entity_id, stage, model_version)
                SELECT 'track', t.id, 'tags', ?
                FROM tracks t
                -- Tag every enriched track; this stage gates clap_audio, which
                -- now embeds all enriched tracks (incl. unknown artist/album).
                WHERE t.enriched IN (1, 2)
                """,
                (tags_version,),
            )
            inserted = cursor.rowcount
            await conn.commit()
        if inserted:
            logger.info("Scheduled %d new tag jobs", inserted)
        return inserted

    async def has_pending_jobs(self, stage: str) -> bool:
        """Return True if any pending jobs exist for the given stage."""
        async with self._open() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM embedding_jobs WHERE status = 'pending' AND stage = ? LIMIT 1",
                (stage,),
            )
            return await cursor.fetchone() is not None

    async def claim_batch(self, stage: str, limit: int) -> list[dict]:
        """
        Atomically claim up to *limit* pending jobs for *stage*.
        Returns the claimed jobs as dicts.
        """
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            await cursor.execute(
                """
                SELECT id, entity_type, entity_id, model_version, attempts
                FROM embedding_jobs
                WHERE stage = ?
                  AND status = 'pending'
                LIMIT ?
                """,
                (stage, limit),
            )
            rows = await cursor.fetchall()
            if not rows:
                return []

            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            await conn.execute(
                f"""
                UPDATE embedding_jobs
                SET status = 'in_progress',
                    attempts = attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                ids,
            )
            await conn.commit()

        return [dict(r) for r in rows]

    async def complete_tags_job(
        self, job_id: int, track_id: str, tags_json: str
    ) -> None:
        """Mark tag job done and merge tags into tracks.tags_predicted."""
        async with self._open() as conn:
            await conn.execute(
                """
                UPDATE tracks
                SET tags_predicted = json_patch(COALESCE(tags_predicted, '{}'), ?)
                WHERE id = ?
                """,
                (tags_json, track_id),
            )
            await conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'done', error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            await conn.commit()

    async def fail_job(self, job_id: int, error: str, max_attempts: int) -> None:
        """Mark job 'failed' if at/above max_attempts, otherwise reset to 'pending'."""
        async with self._open() as conn:
            cursor = await conn.execute(
                "SELECT attempts FROM embedding_jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
            attempts = row[0] if row else max_attempts
            new_status = "failed" if attempts >= max_attempts else "pending"
            await conn.execute(
                """
                UPDATE embedding_jobs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_status, error[:500], job_id),
            )
            await conn.commit()

    async def get_file_path_for_track(self, track_id: str) -> str | None:
        async with self._open() as conn:
            cursor = await conn.execute(
                "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def get_similar_tracks_by_tags(
        self, genres: list[str], limit: int = 100
    ) -> list[str]:
        """Find tracks whose genre tags overlap with the given genres."""
        if not genres:
            return []

        conditions = []
        params: list[str] = []
        for g in genres[:5]:
            conditions.append("t.tags_predicted LIKE ?")
            params.append(f"%{g}%")

        where = " OR ".join(conditions)
        params.append(str(limit))

        async with self._open() as conn:
            cursor = await conn.execute(
                f"""
                SELECT t.id FROM tracks t
                WHERE t.enriched IN (1, 2)
                  AND t.tags_predicted IS NOT NULL
                  AND ({where})
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_tag_coverage_ratio(self) -> float:
        """Return the fraction of enriched tracks that have tags_predicted."""
        async with self._open() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(CASE WHEN tags_predicted IS NOT NULL THEN 1 END),
                    COUNT(*)
                FROM tracks
                WHERE enriched IN (1, 2)
                """
            )
            row = await cursor.fetchone()
        if not row or row[1] == 0:
            return 0.0
        return row[0] / row[1]



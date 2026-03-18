"""
Database layer for the CLAP + Essentia embedding pipeline.

Uses a job queue table (embedding_jobs) for atomic claim/complete/fail
semantics, and sqlite-vec virtual tables for 512-dim KNN search.
"""

import logging
import os

import aiosqlite

from ..config_model import LocalFilesConfig

logger = logging.getLogger(__name__.split(".")[-1])

# Vec table registry: entity_type → (table_name, pk_column)
_VEC_TABLES = {
    "tracks": ("vec_tracks_clap", "track_id"),
    "albums": ("vec_albums_clap", "album_id"),
    "artists": ("vec_artists_clap", "artist_id"),
}

_CLAP_DIMS = 512


class AsyncEmbedderDb:
    """
    Database manager for the CLAP + Essentia embedding pipeline.
    All methods are async and open their own short-lived connections.
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = os.path.expanduser(config.db_path)
        self._vec_available = False

    def _get_connection(self):
        return aiosqlite.connect(self.db_path)

    async def init_db_embeddings(self):
        """
        Create/migrate embedding schema. Safe to call on every startup.
        ALTER TABLEs are wrapped in try/except for idempotency.
        """
        new_columns = [
            # Track columns
            ("tracks", "tags_predicted", "TEXT"),
            ("tracks", "embedding_clap_audio", "BLOB"),
            ("tracks", "embedding_version", "INTEGER DEFAULT 0"),
            ("tracks", "embedded_at", "TIMESTAMP"),
            # Album / artist CLAP aggregate columns
            ("albums", "embedding_clap", "BLOB"),
            ("artists", "embedding_clap", "BLOB"),
        ]

        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            for table, column, col_type in new_columns:
                try:
                    await cursor.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                    )
                    logger.debug("Added column %s.%s", table, column)
                except Exception:
                    pass  # column already exists

            # Job queue table
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_jobs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type   TEXT NOT NULL,
                    entity_id     TEXT NOT NULL,
                    stage         TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    model_version INTEGER NOT NULL DEFAULT 1,
                    attempts      INTEGER NOT NULL DEFAULT 0,
                    error         TEXT,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_type, entity_id, stage, model_version)
                )
                """
            )
            await cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_pending
                    ON embedding_jobs(status, stage)
                    WHERE status IN ('pending', 'failed')
                """
            )

            # Model version registry
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_model_versions (
                    model_name TEXT PRIMARY KEY,
                    version    INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for model_name in ("tags", "clap_audio"):
                await cursor.execute(
                    "INSERT OR IGNORE INTO embedding_model_versions VALUES (?, 1, CURRENT_TIMESTAMP)",
                    (model_name,),
                )

            await conn.commit()

        await self._init_vec_tables()

    async def _init_vec_tables(self):
        """Try to load sqlite-vec and create 512-dim virtual tables."""
        import importlib
        import importlib.util
        import subprocess
        import sys

        if importlib.util.find_spec("sqlite_vec") is None:
            logger.info("Installing missing dependency 'sqlite-vec' via pip …")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "sqlite-vec"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                importlib.invalidate_caches()
            except subprocess.CalledProcessError as e:
                logger.warning("pip install sqlite-vec failed: %s", e.stderr)

        try:
            import sqlite_vec

            async with self._get_connection() as conn:
                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)

                cursor = await conn.cursor()
                for vec_table, pk_col in _VEC_TABLES.values():
                    await cursor.execute(
                        f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table}
                        USING vec0({pk_col} TEXT PRIMARY KEY, embedding float[{_CLAP_DIMS}])
                        """
                    )
                await conn.commit()

            self._vec_available = True
            logger.info("sqlite-vec extension loaded; CLAP vector tables ready")
        except Exception as e:
            logger.warning(
                "sqlite-vec not available (%s); KNN search disabled", e
            )
            self._vec_available = False

    async def _load_vec(self, conn):
        """Load sqlite-vec extension into an open connection."""
        import sqlite_vec
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    async def recover_stale_jobs(self) -> None:
        """Reset in_progress jobs left over from a crashed session."""
        async with self._get_connection() as conn:
            await conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'in_progress'
                """
            )
            await conn.commit()
        logger.info("Stale in_progress jobs reset to pending")

    async def schedule_new_jobs(self, tags_version: int, clap_version: int) -> int:
        """
        Queue tags jobs for enriched tracks without one, and clap_audio jobs
        for tracks whose tags job is 'done'. Returns total jobs inserted.
        """
        inserted = 0
        async with self._get_connection() as conn:
            # Tags jobs: enriched tracks not yet in the queue for this version
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO embedding_jobs
                    (entity_type, entity_id, stage, model_version)
                SELECT 'track', t.id, 'tags', ?
                FROM tracks t
                WHERE t.enriched = 1
                """,
                (tags_version,),
            )
            inserted += cursor.rowcount

            # CLAP audio jobs: tracks with a completed tags job for this version
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO embedding_jobs
                    (entity_type, entity_id, stage, model_version)
                SELECT 'track', j.entity_id, 'clap_audio', ?
                FROM embedding_jobs j
                WHERE j.stage = 'tags'
                  AND j.status = 'done'
                  AND j.model_version = ?
                """,
                (clap_version, tags_version),
            )
            inserted += cursor.rowcount

            await conn.commit()

        if inserted:
            logger.info("Scheduled %d new embedding jobs", inserted)
        return inserted

    async def claim_batch(self, stage: str, limit: int) -> list[dict]:
        """
        Atomically claim up to *limit* pending/failed jobs for *stage*.
        Returns the claimed jobs as dicts.
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            # Select candidates
            await cursor.execute(
                """
                SELECT id, entity_type, entity_id, model_version, attempts
                FROM embedding_jobs
                WHERE stage = ?
                  AND status IN ('pending', 'failed')
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
        """Mark tags job done and write tags_predicted to the track."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE tracks SET tags_predicted = ? WHERE id = ?",
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

    async def complete_clap_job(
        self, job_id: int, track_id: str, blob: bytes, version: int
    ) -> None:
        """Mark CLAP job done and write blob to tracks + vec table."""
        async with self._get_connection() as conn:
            if self._vec_available:
                try:
                    await self._load_vec(conn)
                except Exception:
                    pass

            await conn.execute(
                """
                UPDATE tracks
                SET embedding_clap_audio = ?,
                    embedding_version = ?,
                    embedded_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (blob, version, track_id),
            )

            if self._vec_available:
                try:
                    await conn.execute(
                        "INSERT OR REPLACE INTO vec_tracks_clap (track_id, embedding) VALUES (?, ?)",
                        (track_id, blob),
                    )
                except Exception as e:
                    logger.warning("vec_tracks_clap upsert failed for %s: %s", track_id, e)

            await conn.execute(
                """
                UPDATE embedding_jobs
                SET status = 'done', error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            await conn.commit()

    async def fail_job(
        self, job_id: int, error: str, max_attempts: int
    ) -> None:
        """Increment attempts; mark 'failed' if at/above max_attempts."""
        async with self._get_connection() as conn:
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

    # ------------------------------------------------------------------
    # Aggregate embeddings
    # ------------------------------------------------------------------

    async def get_track_embeddings_for_album(self, album_id: str) -> list[bytes]:
        """Return all non-null CLAP blobs for tracks in the album."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT embedding_clap_audio FROM tracks WHERE album_id = ? AND embedding_clap_audio IS NOT NULL",
                (album_id,),
            )
            rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_track_embeddings_for_artist(self, artist_id: str) -> list[bytes]:
        """Return all non-null CLAP blobs for tracks by the artist."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT embedding_clap_audio FROM tracks WHERE artist_id = ? AND embedding_clap_audio IS NOT NULL",
                (artist_id,),
            )
            rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def update_album_embedding(self, album_id: str, blob: bytes) -> None:
        async with self._get_connection() as conn:
            if self._vec_available:
                try:
                    await self._load_vec(conn)
                except Exception:
                    pass

            await conn.execute(
                "UPDATE albums SET embedding_clap = ? WHERE id = ?",
                (blob, album_id),
            )
            if self._vec_available:
                try:
                    await conn.execute(
                        "INSERT OR REPLACE INTO vec_albums_clap (album_id, embedding) VALUES (?, ?)",
                        (album_id, blob),
                    )
                except Exception as e:
                    logger.warning("vec_albums_clap upsert failed for %s: %s", album_id, e)
            await conn.commit()

    async def update_artist_embedding(self, artist_id: str, blob: bytes) -> None:
        async with self._get_connection() as conn:
            if self._vec_available:
                try:
                    await self._load_vec(conn)
                except Exception:
                    pass

            await conn.execute(
                "UPDATE artists SET embedding_clap = ? WHERE id = ?",
                (blob, artist_id),
            )
            if self._vec_available:
                try:
                    await conn.execute(
                        "INSERT OR REPLACE INTO vec_artists_clap (artist_id, embedding) VALUES (?, ?)",
                        (artist_id, blob),
                    )
                except Exception as e:
                    logger.warning("vec_artists_clap upsert failed for %s: %s", artist_id, e)
            await conn.commit()

    async def get_album_id_for_track(self, track_id: str) -> str | None:
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT album_id FROM tracks WHERE id = ?", (track_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def get_artist_id_for_track(self, track_id: str) -> str | None:
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT artist_id FROM tracks WHERE id = ?", (track_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # KNN search
    # ------------------------------------------------------------------

    async def knn_search(
        self, table: str, blob: bytes, limit: int
    ) -> list[dict]:
        """
        Run KNN search on a vec table. table must be one of the _VEC_TABLES values.
        Returns [{"id": str, "distance": float}] sorted ascending.
        """
        # Resolve table → pk_col
        pk_col = None
        for vec_table, _pk_col in _VEC_TABLES.values():
            if vec_table == table:
                pk_col = _pk_col
                break
        if pk_col is None:
            logger.warning("knn_search: unknown table %s", table)
            return []

        try:
            import sqlite_vec

            async with self._get_connection() as conn:
                await conn.enable_load_extension(True)
                await conn.load_extension(sqlite_vec.loadable_path())
                await conn.enable_load_extension(False)

                cursor = await conn.cursor()
                await cursor.execute(
                    f"SELECT {pk_col}, distance FROM {table}"
                    f" WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (blob, limit),
                )
                rows = await cursor.fetchall()
                return [{"id": row[0], "distance": row[1]} for row in rows]
        except Exception as e:
            logger.debug("knn_search failed for %s: %s", table, e)
            return []

    # ------------------------------------------------------------------
    # Status / observability
    # ------------------------------------------------------------------

    async def get_embedding_coverage(self) -> dict:
        """Return job counts and coverage percentages."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT stage, status, COUNT(*) AS cnt
                FROM embedding_jobs
                GROUP BY stage, status
                """
            )
            rows = await cursor.fetchall()

        stats: dict = {}
        for stage, status, cnt in rows:
            stats.setdefault(stage, {})[status] = cnt

        result = {}
        for stage, counts in stats.items():
            total = sum(counts.values())
            done = counts.get("done", 0)
            result[stage] = {
                "total": total,
                "done": done,
                "pending": counts.get("pending", 0),
                "in_progress": counts.get("in_progress", 0),
                "failed": counts.get("failed", 0),
                "coverage_pct": round(100.0 * done / total, 1) if total else 0.0,
            }
        return result

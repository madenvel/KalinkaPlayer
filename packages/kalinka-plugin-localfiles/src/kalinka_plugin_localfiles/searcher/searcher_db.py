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
from rapidfuzz import fuzz

from ..config_model import LocalFilesConfig
from ..worker_utils import retry_db_locked

logger = logging.getLogger(__name__.split(".")[-1])

# Batch size for FTS population
_INDEX_BATCH_SIZE = 200


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
                    " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
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
                    " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (query_blob, limit),
                )
                rows = await cursor.fetchall()
            return [{"track_id": row[0], "distance": row[1]} for row in rows]
        except Exception as e:
            logger.warning("KNN audio search failed: %s", e)
            return []

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
                WHERE t.enriched IN (1, 2)
                  -- Tag unknown-album tracks too (V/A comps, orphan singles);
                  -- this stage gates clap_audio, which now covers them.
                  -- unknown_artist tracks stay excluded.
                  AND t.artist_id != 'unknown_artist'
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
        """Mark tag job done, merge tags into tracks.tags_predicted, and
        reset search_indexed_at so the track gets re-indexed in FTS."""
        async with self._open() as conn:
            await conn.execute(
                """
                UPDATE tracks
                SET tags_predicted = json_patch(COALESCE(tags_predicted, '{}'), ?),
                    search_indexed_at = NULL
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

    # ------------------------------------------------------------------
    # FTS population (offline indexing)
    # ------------------------------------------------------------------

    async def count_unindexed_tracks(self) -> int:
        """Return the number of tracks that need FTS indexing."""
        async with self._open() as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM tracks
                WHERE enriched IN (1, 2)
                  AND search_indexed_at IS NULL
                """
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def index_batch(self, limit: int = _INDEX_BATCH_SIZE) -> int:
        """
        Index a batch of un-indexed tracks into the FTS5 table.
        Returns the number of tracks indexed (0 means no work left).
        """
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT t.id, t.title, ar.name AS artist_name, al.title AS album_title,
                       t.tags_predicted
                FROM tracks t
                LEFT JOIN artists ar ON t.artist_id = ar.id
                LEFT JOIN albums  al ON t.album_id  = al.id
                WHERE t.enriched IN (1, 2)
                  AND t.search_indexed_at IS NULL
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            if not rows:
                return 0

            indexed = 0
            for row in rows:
                track_id = row["id"]
                title = row["title"] or ""
                artist_name = row["artist_name"] or ""
                album_title = row["album_title"] or ""
                genre_tags = _extract_genre_text(row["tags_predicted"])

                await conn.execute(
                    "DELETE FROM fts_tracks WHERE track_id = ?",
                    (track_id,),
                )
                await conn.execute(
                    """
                    INSERT INTO fts_tracks (track_id, title, artist_name, album_title, genre_tags)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (track_id, title, artist_name, album_title, genre_tags),
                )

                await conn.execute(
                    "UPDATE tracks SET search_indexed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (track_id,),
                )
                indexed += 1

            await conn.commit()
        if indexed:
            logger.info("FTS indexed %d tracks", indexed)
        return indexed

    async def reconcile_deleted_tracks(self) -> int:
        """Remove FTS entries for tracks that no longer exist."""
        async with self._open() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM fts_tracks
                WHERE track_id NOT IN (SELECT id FROM tracks)
                """
            )
            removed = cursor.rowcount
            if removed:
                await conn.commit()
                logger.info("Removed %d stale FTS entries", removed)
        return removed

    async def invalidate_stale_indexes(self) -> int:
        """Reset search_indexed_at for tracks whose metadata changed."""
        async with self._open() as conn:
            cursor = await conn.execute(
                """
                UPDATE tracks
                SET search_indexed_at = NULL
                WHERE search_indexed_at IS NOT NULL
                  AND enriched IN (1, 2)
                  AND modified_time > search_indexed_at
                """
            )
            invalidated = cursor.rowcount
            if invalidated:
                await conn.commit()
                logger.info(
                    "Invalidated %d stale FTS entries for re-indexing", invalidated
                )
        return invalidated

    # ------------------------------------------------------------------
    # FTS5 search
    # ------------------------------------------------------------------

    async def fts_search(
        self,
        text_query: str,
        raw_query: str,
        limit: int = 100,
        min_score: int = 72,
        overfetch: int = 5,
    ) -> list[dict]:
        """
        Full-text search on the FTS5 table, with rapidfuzz re-ranking.

        Returns [{"track_id": str, "rank": float, "exact": bool}] sorted
        by relevance, ``rank`` negated so lower is better (matches the
        convention of the rest of the pipeline). ``exact`` is True when
        the query equals one of the track's fields (title / artist /
        album) case-insensitively — the caller floats those above pure
        semantic neighbours so an exact artist/album hit always wins.

        FTS5 is used purely as a fast candidate fetcher (OR-mode, broad
        recall). Each candidate is then scored with
        ``rapidfuzz.fuzz.WRatio`` against ``title``, ``artist_name``,
        and ``album_title`` *separately* — the best per-field score
        wins — and dropped if it falls below ``min_score`` (0–100).
        Per-field rather than concatenated because WRatio is
        length-sensitive: a short query ("yesterday") against a long
        concatenated haystack scores lower than against the title
        alone. This kills single-token coincidental hits ("tonight"
        matching "Make Tonight All Mine" for the query "something
        melancholic for tonight") and is naturally typo-tolerant on
        the metadata side.
        """
        if not text_query.strip():
            return []

        fts_query = _build_fts_query(text_query, join="OR")
        logger.info("fts_search: raw=%r built=%r", text_query, fts_query)
        if not fts_query:
            logger.info("fts_search: query built to empty string — no results")
            return []

        candidate_limit = limit * overfetch
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            try:
                cursor = await conn.execute(
                    """
                    SELECT track_id, title, artist_name, album_title
                    FROM fts_tracks
                    WHERE fts_tracks MATCH ?
                    LIMIT ?
                    """,
                    (fts_query, candidate_limit),
                )
                rows = await cursor.fetchall()
            except Exception as e:
                logger.warning("FTS search failed for query %r: %s", fts_query, e)
                return []

        fuzz_target = raw_query.strip() or text_query
        norm_target = fuzz_target.casefold().strip()
        scored: list[dict] = []
        for row in rows:
            # Score against each field separately and keep the best. A
            # single concatenated haystack penalises short queries
            # because WRatio is length-sensitive: e.g. "yesterday" vs
            # the full "Yesterday | Beatles | Help!" string drags lower
            # than "yesterday" vs the title field on its own.
            best = 0
            exact = False
            for field in (row["title"], row["artist_name"], row["album_title"]):
                if not field:
                    continue
                s = fuzz.WRatio(fuzz_target, field)
                if s > best:
                    best = s
                if field.casefold().strip() == norm_target:
                    exact = True
            if best >= min_score:
                scored.append(
                    {
                        "track_id": row["track_id"],
                        "rank": -float(best),
                        "exact": exact,
                    }
                )

        scored.sort(key=lambda r: r["rank"])
        logger.info(
            "fts_search: %d candidates, %d kept after WRatio>=%d",
            len(rows),
            len(scored),
            min_score,
        )
        return scored[:limit]

    # ------------------------------------------------------------------
    # Tag lookups (for re-ranking and "songs like this")
    # ------------------------------------------------------------------

    async def get_track_tags(self, track_id: str) -> dict | None:
        """Return parsed tags_predicted JSON for a track, or None."""
        async with self._open() as conn:
            cursor = await conn.execute(
                "SELECT tags_predicted FROM tracks WHERE id = ?",
                (track_id,),
            )
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    async def get_tracks_tags_bulk(self, track_ids: list[str]) -> dict[str, dict]:
        """Return {track_id: tags_dict} for a list of track IDs."""
        if not track_ids:
            return {}
        async with self._open() as conn:
            placeholders = ",".join("?" * len(track_ids))
            cursor = await conn.execute(
                f"SELECT id, tags_predicted FROM tracks WHERE id IN ({placeholders})",
                track_ids,
            )
            rows = await cursor.fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            tid, raw = row[0], row[1]
            if raw:
                try:
                    result[tid] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    async def get_track_album_artist(self, track_id: str) -> dict | None:
        """Return album_id and artist_id for a track."""
        async with self._open() as conn:
            cursor = await conn.execute(
                "SELECT album_id, artist_id FROM tracks WHERE id = ?",
                (track_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {"album_id": row[0], "artist_id": row[1]}

    async def get_tracks_album_artist_bulk(
        self, track_ids: list[str]
    ) -> dict[str, dict]:
        """Return {track_id: {album_id, artist_id}} for a list of track IDs."""
        if not track_ids:
            return {}
        async with self._open() as conn:
            placeholders = ",".join("?" * len(track_ids))
            cursor = await conn.execute(
                f"SELECT id, album_id, artist_id FROM tracks WHERE id IN ({placeholders})",
                track_ids,
            )
            rows = await cursor.fetchall()
        return {row[0]: {"album_id": row[1], "artist_id": row[2]} for row in rows}

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_genre_text(tags_predicted_json: str | None) -> str:
    """Extract space-separated genre labels from a tags_predicted JSON string."""
    if not tags_predicted_json:
        return ""
    try:
        tags = json.loads(tags_predicted_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    genres = tags.get("genres") or []
    labels = [g.get("label", "") for g in genres if isinstance(g, dict)]
    return " ".join(labels)


def _build_fts_query(text: str, join: str = "AND") -> str:
    """Build an FTS5 query string from user text.

    *join* controls how tokens are combined: "AND" (default, precise)
    or "OR" (broad recall).
    """
    cleaned = (
        text.replace('"', " ").replace("*", " ").replace("(", " ").replace(")", " ")
    )
    tokens = cleaned.split()
    if not tokens:
        return ""
    parts = [f'"{t}"' for t in tokens if len(t) >= 2]
    if not parts:
        return ""
    return f" {join} ".join(parts)

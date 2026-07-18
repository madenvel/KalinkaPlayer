import os
from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite
import logging
import time
from typing import List, Dict, Optional, Any, Tuple

from ..config_model import LocalFilesConfig
from ..worker_utils import retry_db_locked, stage_status

logger = logging.getLogger(__name__.split(".")[-1])

# Key under which the enrichment fingerprint is stored in ``enricher_state``.
ENRICHMENT_FINGERPRINT_KEY = "enrichment_fingerprint"


@retry_db_locked
class AsyncEnricherDb:
    """
    Asynchronous database manager specifically for the metadata enricher.
    Handles operations required for enrichment of music metadata.
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = Path(config.db_path).expanduser().resolve()
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        self._columns_cache: Dict[str, set] = {}

        # Ensure directories exist
        db_dir = os.path.dirname(self.db_path)
        if db_dir:  # Only create directory if path is not empty
            os.makedirs(db_dir, exist_ok=True)
        os.makedirs(self.artwork_path, exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "album"), exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "artist"), exist_ok=True)

    def _get_connection(self):
        """Get a database connection with row factory"""
        return aiosqlite.connect(self.db_path, timeout=5.0)

    @asynccontextmanager
    async def _open(self):
        async with self._get_connection() as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            yield conn

    async def get_track_by_id(self, track_id: str) -> Optional[Dict]:
        """Get track information by ID"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name 
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.id = ?
            """,
                (track_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_tracks_by_ids(self, track_ids: List[str]) -> List[Dict]:
        """Get track information by IDs"""
        if not track_ids:
            return []

        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            placeholders = ", ".join("?" for _ in track_ids)
            await cursor.execute(
                f"""
                SELECT t.*, a.title as album_title, ar.name as artist_name 
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.id IN ({placeholders})
            """,
                track_ids,
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_artist_by_id(self, artist_id: str) -> Optional[Dict]:
        """Get artist information by ID"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_artist_by_mbid(self, mbid: str) -> Optional[Dict]:
        """Get artist information by MusicBrainz ID"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT * FROM artists WHERE mbid = ? LIMIT 1", (mbid,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        """Get album information by ID"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.id = ?
            """,
                (album_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_album_by_mbid(self, mbid: str) -> Optional[Dict]:
        """Get album information by MusicBrainz ID"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.mbid = ? LIMIT 1
                """,
                (mbid,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    async def _reset_failed_rows(cursor) -> Dict[str, int]:
        """Flip every ``enriched=2`` (FAILED) row back to ``enriched=0``
        on the given cursor (no commit). Returns per-entity row counts.

        Albums carrying *generated* artwork are also re-opened, with the
        generated cover cleared: this sweep only runs when the enrichment
        setup changed, and a changed setup may now find a real cover that
        the generated one would otherwise mask (the art-fetching plugins
        skip albums whose ``image_url`` is set). If nothing better turns
        up, the deterministic generator re-derives the identical cover.
        """
        counts: Dict[str, int] = {"artists": 0, "albums": 0, "tracks": 0}
        await cursor.execute(
            "UPDATE albums SET enriched = 0, image_url = NULL, "
            "image_generated = 0 WHERE image_generated = 1"
        )
        counts["albums"] = cursor.rowcount or 0
        for table in ("artists", "albums", "tracks"):
            await cursor.execute(f"UPDATE {table} SET enriched = 0 WHERE enriched = 2")
            counts[table] += cursor.rowcount or 0
        return counts

    async def reset_failed_to_retry(self) -> Dict[str, int]:
        """Flip every ``enriched=2`` (FAILED) row back to ``enriched=0`` so
        the next enrichment pass picks it up. Unconditional — see
        :meth:`reset_failed_for_fingerprint` for the gated variant the
        enricher actually uses at startup.

        Returns row counts per entity for logging.
        """
        async with self._open() as conn:
            cursor = await conn.cursor()
            counts = await self._reset_failed_rows(cursor)
            await conn.commit()
        return counts

    async def get_enrichment_fingerprint(self) -> Optional[str]:
        """Return the enrichment fingerprint stored on the previous run,
        or ``None`` if none has been recorded yet."""
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT value FROM enricher_state WHERE key = ?",
                (ENRICHMENT_FINGERPRINT_KEY,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def reset_failed_for_fingerprint(
        self, fingerprint: str
    ) -> Optional[Dict[str, int]]:
        """Gated FAILED-row reset, run once at enricher-process startup.

        Reset previously-FAILED rows back to NOT_ENRICHED *only* when
        ``fingerprint`` differs from the one recorded on the last run
        (or none is recorded yet — first run after this feature ships,
        or a fresh DB), then persist ``fingerprint``. The fingerprint
        encodes the active plugin set, each plugin's
        ``ENRICHER_VERSION``, and its match-affecting config (see
        ``MetadataEnricher.compute_fingerprint``), so a reset happens
        exactly when the enrichment setup changed in a way that could
        flip a FAILED row to enriched — not on every restart.

        Read, reset and store run in a single transaction so a crash
        can't leave the fingerprint advanced while the rows stay FAILED.

        Returns the per-entity reset counts when a reset happened, or
        ``None`` when the fingerprint was unchanged and FAILED rows were
        left intact.
        """
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT value FROM enricher_state WHERE key = ?",
                (ENRICHMENT_FINGERPRINT_KEY,),
            )
            row = await cursor.fetchone()
            stored = row[0] if row else None
            if stored == fingerprint:
                return None

            counts = await self._reset_failed_rows(cursor)
            await cursor.execute(
                "INSERT INTO enricher_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (ENRICHMENT_FINGERPRINT_KEY, fingerprint),
            )
            await conn.commit()
        return counts

    async def count_distinct_artists_in_folder(self, parent_dir: str) -> int:
        """Return the number of distinct real artists with at least one
        track whose ``file_path`` is in ``parent_dir`` (non-recursive).

        ``unknown_artist`` is excluded so it doesn't deflate or inflate
        the count depending on how many untagged tracks happen to be
        in the folder. Used by ``filesystem_fallback`` to decide
        whether to leave a track in ``unknown_album`` (V/A folder) or
        anchor it to a derived album (single-artist folder).
        """
        if not parent_dir:
            return 0
        # Match "<parent_dir>/<file>" but NOT "<parent_dir>/<subdir>/<file>"
        # so siblings of the same album folder count but disc subdirs
        # don't double-count tracks from a separate logical folder.
        prefix = parent_dir.rstrip("/") + "/"
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT COUNT(DISTINCT artist_id) FROM tracks
                WHERE file_path LIKE ? AND file_path NOT LIKE ?
                  AND artist_id != 'unknown_artist'
                """,
                (prefix + "%", prefix + "%/%"),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def count_tracks_in_folder(self, parent_dir: str) -> int:
        """Return the number of tracks whose ``file_path`` is directly
        in ``parent_dir`` (non-recursive). Companion to
        ``count_distinct_artists_in_folder`` — together they let
        callers compute the unique-artist-per-track ratio used by the
        V/A detection criteria.
        """
        if not parent_dir:
            return 0
        prefix = parent_dir.rstrip("/") + "/"
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT COUNT(*) FROM tracks
                WHERE file_path LIKE ? AND file_path NOT LIKE ?
                """,
                (prefix + "%", prefix + "%/%"),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def get_album_track_titles(self, album_id: str) -> List[str]:
        """Titles of an album's tracks in disc/track order.

        Feeds the procedural artwork generator's fallback album-family
        signature, so the ordering must be deterministic.
        """
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT title FROM tracks
                WHERE album_id = ?
                ORDER BY disc_number, track_number, title
                """,
                (album_id,),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows if row[0]]

    async def get_non_enriched_artists(self, limit: int = 50) -> List[Dict]:
        """Get artists that haven't been enriched yet"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT * FROM artists
                WHERE enriched = 0
                  AND id != 'unknown_artist'
                LIMIT ?
            """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_non_enriched_albums(self, limit: int = 50) -> List[Dict]:
        """Get albums that haven't been enriched yet"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.enriched = 0 AND a.id != 'unknown_album'
                LIMIT ?
            """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_non_enriched_tracks(self, limit: int = 50) -> List[Dict]:
        """Get tracks that haven't been enriched yet"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.enriched = 0
                LIMIT ?
            """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_enrichment_coverage(self) -> Dict:
        """Aggregate enrichment progress across artists + albums + tracks,
        in the same shape as the embedder's per-stage coverage
        (``enriched`` values per EnrichmentStatus: 1 = done, 2 = failed,
        0 = pending; the enricher pulls one item at a time, so there is no
        separate in-progress state).

        Each count mirrors the WHERE clause of the matching
        ``get_non_enriched_*`` picker above — anything the picker can never
        select (sentinel rows, albums with a dangling artist_id) must not be
        counted, or the stage would read as pending forever."""
        queries = (
            ("SELECT COUNT(*), SUM(enriched = 1), SUM(enriched = 2) "
             "FROM artists WHERE id != ?", ("unknown_artist",)),
            ("SELECT COUNT(*), SUM(a.enriched = 1), SUM(a.enriched = 2) "
             "FROM albums a JOIN artists ar ON a.artist_id = ar.id "
             "WHERE a.id != ?", ("unknown_album",)),
            ("SELECT COUNT(*), SUM(enriched = 1), SUM(enriched = 2) "
             "FROM tracks", ()),
        )
        total = done = failed = 0
        async with self._open() as conn:
            cursor = await conn.cursor()
            for sql, params in queries:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                total += row[0]
                done += row[1] or 0
                failed += row[2] or 0
        return stage_status(total, done, failed=failed)

    async def search_artists(
        self, query: str, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search artists by query"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            search_term = f"%{query}%"

            # Get total count
            await cursor.execute(
                "SELECT COUNT(*) as count FROM artists WHERE name LIKE ?",
                (search_term,),
            )
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            # Get results
            await cursor.execute(
                """
                SELECT * FROM artists
                WHERE name LIKE ?
                ORDER BY name
                LIMIT ?
            """,
                (search_term, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows], total

    async def _table_columns(self, conn, table: str) -> set:
        """Return the column names for *table*, cached (schema is static)."""
        cached = self._columns_cache.get(table)
        if cached is not None:
            return cached
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}  # name at index 1
        self._columns_cache[table] = columns
        return columns

    async def _update(self, table: str, entity_id: str, data: Dict[str, Any]) -> None:
        """Update a row by id, ignoring keys that aren't real columns."""
        async with self._open() as conn:
            valid_columns = await self._table_columns(conn, table)
            filtered_data = {k: v for k, v in data.items() if k in valid_columns}
            if not filtered_data:
                logger.debug(f"No valid columns to update for {table} {entity_id}")
                return

            fields = [f"{key} = ?" for key in filtered_data]
            values = list(filtered_data.values()) + [entity_id]
            query = f"UPDATE {table} SET {', '.join(fields)} WHERE id = ?"
            await conn.execute(query, values)
            await conn.commit()

            filtered_out = set(data) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for {table} {entity_id}: "
                    f"{', '.join(filtered_out)}"
                )

    async def update_artist(self, artist_id: str, data: Dict[str, Any]) -> None:
        """Update artist information"""
        await self._update("artists", artist_id, data)

    async def update_album(self, album_id: str, data: Dict[str, Any]) -> None:
        """Update album information"""
        await self._update("albums", album_id, data)

    async def update_track(self, track_id: str, data: Dict[str, Any]) -> None:
        """Update track information"""
        await self._update("tracks", track_id, data)

    async def update_album_stats(self, album_id: str) -> None:
        """Update album statistics (track count and duration)"""
        async with self._open() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                """
                UPDATE albums SET
                track_count = (SELECT COUNT(*) FROM tracks WHERE album_id = ?),
                duration = (SELECT SUM(duration) FROM tracks WHERE album_id = ?)
                WHERE id = ?
            """,
                (album_id, album_id, album_id),
            )
            await conn.commit()

    async def insert_artist(self, data: Dict[str, Any]) -> None:
        """Insert a new artist"""
        async with self._open() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO artists ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

    async def insert_album(self, data: Dict[str, Any]) -> None:
        """Insert a new album"""
        async with self._open() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO albums ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

    async def insert_track(self, data: Dict[str, Any]) -> None:
        """Insert a new track"""
        async with self._open() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO tracks ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

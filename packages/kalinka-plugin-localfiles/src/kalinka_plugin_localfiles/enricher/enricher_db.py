import os
from contextlib import asynccontextmanager
from pathlib import Path
import aiosqlite
import logging
import time
from typing import List, Dict, Optional, Any, Tuple

from ..config_model import LocalFilesConfig
from ..worker_utils import retry_db_locked

logger = logging.getLogger(__name__.split(".")[-1])


@retry_db_locked
class AsyncEnricherDb:
    """
    Asynchronous database manager specifically for the metadata enricher.
    Handles operations required for enrichment of music metadata.
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = Path(config.db_path).expanduser().resolve()
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()

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

    async def reset_failed_to_retry(self) -> Dict[str, int]:
        """Flip every ``enriched=2`` (FAILED) row back to ``enriched=0`` so
        the next enrichment pass picks it up.

        Called once at enricher-process startup so that, after the user
        deploys updated plugin code (or a new MB matcher tier), the
        already-failed rows get one more chance instead of staying
        stuck. Bounded to once-per-process: if the enricher is hammered
        with ``enrich`` commands inside the same lifetime the FAILED
        rows aren't reset again (that's the caller's responsibility).

        Returns row counts per entity for logging.
        """
        counts: Dict[str, int] = {"artists": 0, "albums": 0, "tracks": 0}
        async with self._open() as conn:
            cursor = await conn.cursor()
            for table in ("artists", "albums", "tracks"):
                await cursor.execute(
                    f"UPDATE {table} SET enriched = 0 WHERE enriched = 2"
                )
                counts[table] = cursor.rowcount or 0
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

    async def update_artist(self, artist_id: str, data: Dict[str, Any]) -> None:
        """Update artist information"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            # Get artist table column names
            await cursor.execute("PRAGMA table_info(artists)")
            rows = await cursor.fetchall()
            valid_columns = {row["name"] for row in rows}

            # Filter out data keys that don't exist in the artists table
            filtered_data = {k: v for k, v in data.items() if k in valid_columns}

            if not filtered_data:
                logger.debug(f"No valid columns to update for artist {artist_id}")
                return

            # Build the SET clause with only valid columns
            fields = []
            values = []
            for key, value in filtered_data.items():
                fields.append(f"{key} = ?")
                values.append(value)

            # Add artist_id to the values
            values.append(artist_id)

            query = f"UPDATE artists SET {', '.join(fields)} WHERE id = ?"
            await cursor.execute(query, values)
            await conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for artist {artist_id}: {', '.join(filtered_out)}"
                )

    async def update_album(self, album_id: str, data: Dict[str, Any]) -> None:
        """Update album information"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            # Get album table column names
            await cursor.execute("PRAGMA table_info(albums)")
            rows = await cursor.fetchall()
            valid_columns = {row["name"] for row in rows}

            # Filter out data keys that don't exist in the albums table
            filtered_data = {k: v for k, v in data.items() if k in valid_columns}

            if not filtered_data:
                logger.debug(f"No valid columns to update for album {album_id}")
                return

            # Build the SET clause with only valid columns
            fields = []
            values = []
            for key, value in filtered_data.items():
                fields.append(f"{key} = ?")
                values.append(value)

            # Add album_id to the values
            values.append(album_id)

            query = f"UPDATE albums SET {', '.join(fields)} WHERE id = ?"
            await cursor.execute(query, values)
            await conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for album {album_id}: {', '.join(filtered_out)}"
                )

    async def update_track(self, track_id: str, data: Dict[str, Any]) -> None:
        """Update track information"""
        async with self._open() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()

            # Get track table column names
            await cursor.execute("PRAGMA table_info(tracks)")
            rows = await cursor.fetchall()
            valid_columns = {row["name"] for row in rows}

            # Filter out data keys that don't exist in the tracks table
            filtered_data = {k: v for k, v in data.items() if k in valid_columns}

            if not filtered_data:
                logger.debug(f"No valid columns to update for track {track_id}")
                return

            # Build the SET clause with only valid columns
            fields = []
            values = []
            for key, value in filtered_data.items():
                fields.append(f"{key} = ?")
                values.append(value)

            # Add track_id to the values
            values.append(track_id)

            query = f"UPDATE tracks SET {', '.join(fields)} WHERE id = ?"
            await cursor.execute(query, values)
            await conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for track {track_id}: {', '.join(filtered_out)}"
                )

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

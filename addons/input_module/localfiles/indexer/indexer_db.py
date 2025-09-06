import os
import aiosqlite
import logging
from typing import List, Dict, Optional, Any, Tuple
import time

from ...localfiles.config_model import LocalFilesConfig

logger = logging.getLogger(__name__.split(".")[-1])


class AsyncIndexerDb:
    """
    Asynchronous database manager specifically for the file indexer.
    Handles operations required for scanning and indexing music files.
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = os.path.expanduser(config.db_path)
        self.artwork_path = os.path.expanduser(config.artwork_path)

        # Ensure directories exist
        db_dir = os.path.dirname(self.db_path)
        if db_dir:  # Only create directory if path is not empty
            os.makedirs(db_dir, exist_ok=True)
        os.makedirs(self.artwork_path, exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "album"), exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "artist"), exist_ok=True)

    async def init_db(self):
        """Initialize the database schema if it doesn't exist"""
        async with self._get_connection() as conn:
            try:
                cursor = await conn.cursor()

                # Create artists table
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artists (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        mbid TEXT,
                        image_url TEXT,
                        enriched INTEGER DEFAULT 0,
                        match_score INTEGER,
                        match_similarity INTEGER,
                        last_updated INTEGER
                    )
                """
                )

                # Create albums table
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS albums (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        artist_id TEXT,
                        year INTEGER,
                        genre TEXT,
                        image_url TEXT,
                        mbid TEXT,
                        track_count INTEGER DEFAULT 0,
                        duration INTEGER DEFAULT 0,
                        enriched INTEGER DEFAULT 0,
                        match_score INTEGER,
                        match_similarity INTEGER,
                        last_updated INTEGER,
                        FOREIGN KEY (artist_id) REFERENCES artists (id)
                    )
                """
                )

                # Create tracks table
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tracks (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        album_id TEXT,
                        artist_id TEXT,
                        duration INTEGER,
                        track_number INTEGER,
                        disc_number INTEGER,
                        file_path TEXT NOT NULL,
                        format TEXT NOT NULL,
                        file_size BIGINT,
                        modified_time INTEGER,
                        mbid TEXT,
                        match_score INTEGER,
                        match_similarity INTEGER,
                        replaygain_peak REAL,
                        replaygain_gain REAL,
                        enriched INTEGER DEFAULT 0,
                        last_updated INTEGER,
                        FOREIGN KEY (album_id) REFERENCES albums (id),
                        FOREIGN KEY (artist_id) REFERENCES artists (id)
                    )
                """
                )

                # Create playlists table
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS playlists (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        track_count INTEGER DEFAULT 0,
                        image_url TEXT,
                        duration INTEGER DEFAULT 0,
                        created_by TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        last_updated INTEGER NOT NULL
                    )
                """
                )

                # Create playlist_tracks table
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS playlist_tracks (
                        playlist_track_id TEXT PRIMARY KEY,
                        playlist_id TEXT,
                        track_id TEXT,
                        position INTEGER NOT NULL,
                        added_at INTEGER NOT NULL,
                        FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
                        FOREIGN KEY (track_id) REFERENCES tracks (id) ON DELETE CASCADE
                    )
                """
                )

                # Create index for efficient lookups by playlist_id
                await cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist_id 
                    ON playlist_tracks (playlist_id, position)
                """
                )

                # Create recently_added view
                await cursor.execute(
                    """
                    CREATE VIEW IF NOT EXISTS recently_added AS
                    SELECT * FROM tracks
                    ORDER BY last_updated DESC
                """
                )

                # Insert default "unknown" entries if they don't exist
                current_time = int(time.time())
                await cursor.execute(
                    """
                    INSERT OR IGNORE INTO artists (id, name, last_updated)
                    VALUES ('unknown_artist', 'Unknown Artist', ?)
                """,
                    (current_time,),
                )

                await cursor.execute(
                    """
                    INSERT OR IGNORE INTO albums (id, title, artist_id, last_updated)
                    VALUES ('unknown_album', 'Unknown Album', 'unknown_artist', ?)
                """,
                    (current_time,),
                )

                await conn.commit()

                logger.info("Database initialized successfully")
            except Exception as e:
                logger.error(f"Error initializing database: {str(e)}")
                await conn.rollback()
                raise

    def _get_connection(self):
        """Get a database connection with row factory"""
        return aiosqlite.connect(self.db_path)

    async def get_track_by_path(self, file_path: str) -> Optional[Dict]:
        """Get track information by file path"""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT * FROM tracks WHERE file_path = ?
            """,
                (file_path,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_track_by_id(self, track_id: str) -> Optional[Dict]:
        """Get track information by ID"""
        async with self._get_connection() as conn:
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

    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        """Get album information by ID"""
        async with self._get_connection() as conn:
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

    async def get_artist_by_id(self, artist_id: str) -> Optional[Dict]:
        """Get artist information by ID"""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_track(self, track_id: str, data: Dict[str, Any]) -> None:
        """Update track information"""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            # Get track table column names
            await cursor.execute("PRAGMA table_info(tracks)")
            rows = await cursor.fetchall()
            valid_columns = {row[1] for row in rows}  # Column name is at index 1

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

    async def insert_track(self, data: Dict[str, Any]) -> None:
        """Insert a new track"""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO tracks ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

    async def insert_album(self, data: Dict[str, Any]) -> None:
        """Insert a new album"""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO albums ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

    async def insert_artist(self, data: Dict[str, Any]) -> None:
        """Insert a new artist"""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO artists ({fields}) VALUES ({placeholders})"
            await cursor.execute(query, values)
            await conn.commit()

    async def update_album_stats(self, album_id: str) -> None:
        """Update album statistics (track count and duration)"""
        async with self._get_connection() as conn:
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

    async def get_all_tracks(self) -> List[Dict]:
        """Get all tracks in the database"""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute(
                """
                SELECT * FROM tracks
                ORDER BY file_path
            """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_track(self, track_id: str) -> bool:
        """Delete a track by ID. Returns True if successful."""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            deleted = cursor.rowcount > 0
            await conn.commit()
            return deleted

    async def delete_orphaned_albums_and_artists(self) -> Tuple[int, int]:
        """Delete albums and artists that have no tracks referencing them.
        Returns tuple of (deleted_albums_count, deleted_artists_count)"""
        async with self._get_connection() as conn:
            cursor = await conn.cursor()

            # Get albums with no tracks
            await cursor.execute(
                """
                SELECT id FROM albums 
                WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)
                AND id != 'unknown_album'
                """
            )
            rows = await cursor.fetchall()
            orphaned_albums = [row[0] for row in rows]

            # Delete orphaned albums
            deleted_albums = 0
            if orphaned_albums:
                albums_placeholders = ", ".join(["?"] * len(orphaned_albums))
                await cursor.execute(
                    f"DELETE FROM albums WHERE id IN ({albums_placeholders})",
                    orphaned_albums,
                )
                deleted_albums = cursor.rowcount

            # Get artists with no tracks or albums
            await cursor.execute(
                """
                SELECT id FROM artists 
                WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks)
                AND id NOT IN (SELECT DISTINCT artist_id FROM albums)
                AND id != 'unknown_artist'
                """
            )
            rows = await cursor.fetchall()
            orphaned_artists = [row[0] for row in rows]

            # Delete orphaned artists
            deleted_artists = 0
            if orphaned_artists:
                artists_placeholders = ", ".join(["?"] * len(orphaned_artists))
                await cursor.execute(
                    f"DELETE FROM artists WHERE id IN ({artists_placeholders})",
                    orphaned_artists,
                )
                deleted_artists = cursor.rowcount

            await conn.commit()
            return deleted_albums, deleted_artists

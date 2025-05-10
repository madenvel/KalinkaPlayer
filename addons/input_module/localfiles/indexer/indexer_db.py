import os
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import time

logger = logging.getLogger(__name__.split(".")[-1])


class IndexerDb:
    """
    Database manager specifically for the file indexer.
    Handles operations required for scanning and indexing music files.
    """

    def __init__(self, config):
        self.db_path = config["db_path"]
        self.artwork_path = config["artwork_path"]

        # Ensure directories exist
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.artwork_path, exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "album"), exist_ok=True)
        os.makedirs(os.path.join(self.artwork_path, "artist"), exist_ok=True)

        self._init_db()

    def _init_db(self):
        """Initialize the database schema if it doesn't exist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Create artists table
            cursor.execute(
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS albums (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    artist_id TEXT,
                    year INTEGER,
                    genre TEXT,
                    cover_art TEXT,
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    album_id TEXT,
                    artist_id TEXT,
                    duration INTEGER,
                    track_number INTEGER,
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    track_count INTEGER DEFAULT 0,
                    duration INTEGER DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_updated INTEGER NOT NULL
                )
            """
            )

            # Create playlist_tracks table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_id TEXT,
                    track_id TEXT,
                    position INTEGER NOT NULL,
                    added_at INTEGER NOT NULL,
                    PRIMARY KEY (playlist_id, track_id),
                    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks (id) ON DELETE CASCADE
                )
            """
            )

            # Create recently_added view
            cursor.execute(
                """
                CREATE VIEW IF NOT EXISTS recently_added AS
                SELECT * FROM tracks
                ORDER BY last_updated DESC
            """
            )

            # Insert default "unknown" entries if they don't exist
            cursor.execute(
                """
                INSERT OR IGNORE INTO artists (id, name, last_updated)
                VALUES ('unknown_artist', 'Unknown Artist', ?)
            """,
                (int(time.time()),),
            )

            cursor.execute(
                """
                INSERT OR IGNORE INTO albums (id, title, artist_id, last_updated)
                VALUES ('unknown_album', 'Unknown Album', 'unknown_artist', ?)
            """,
                (int(time.time()),),
            )

            conn.commit()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def _get_connection(self):
        """Get a database connection with row factory"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_track_by_path(self, file_path: str) -> Optional[Dict]:
        """Get track information by file path"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM tracks WHERE file_path = ?
            """,
                (file_path,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_track_by_id(self, track_id: str) -> Optional[Dict]:
        """Get track information by ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name 
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.id = ?
            """,
                (track_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        """Get album information by ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.id = ?
            """,
                (album_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_artist_by_id(self, artist_id: str) -> Optional[Dict]:
        """Get artist information by ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_track(self, track_id: str, data: Dict[str, Any]) -> None:
        """Update track information"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get track table column names
            cursor.execute("PRAGMA table_info(tracks)")
            valid_columns = {row["name"] for row in cursor.fetchall()}

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
            cursor.execute(query, values)
            conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for track {track_id}: {', '.join(filtered_out)}"
                )

        finally:
            conn.close()

    def insert_track(self, data: Dict[str, Any]) -> None:
        """Insert a new track"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO tracks ({fields}) VALUES ({placeholders})"
            cursor.execute(query, values)
            conn.commit()
        finally:
            conn.close()

    def insert_album(self, data: Dict[str, Any]) -> None:
        """Insert a new album"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO albums ({fields}) VALUES ({placeholders})"
            cursor.execute(query, values)
            conn.commit()
        finally:
            conn.close()

    def insert_artist(self, data: Dict[str, Any]) -> None:
        """Insert a new artist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Build the query
            fields = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            values = list(data.values())

            query = f"INSERT OR REPLACE INTO artists ({fields}) VALUES ({placeholders})"
            cursor.execute(query, values)
            conn.commit()
        finally:
            conn.close()

    def update_album_stats(self, album_id: str) -> None:
        """Update album statistics (track count and duration)"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE albums SET
                track_count = (SELECT COUNT(*) FROM tracks WHERE album_id = ?),
                duration = (SELECT SUM(duration) FROM tracks WHERE album_id = ?)
                WHERE id = ?
            """,
                (album_id, album_id, album_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_tracks(self) -> List[Dict]:
        """Get all tracks in the database"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM tracks
                ORDER BY file_path
            """
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def delete_track(self, track_id: str) -> bool:
        """Delete a track by ID. Returns True if successful."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()

    def delete_orphaned_albums_and_artists(self) -> Tuple[int, int]:
        """Delete albums and artists that have no tracks referencing them.
        Returns tuple of (deleted_albums_count, deleted_artists_count)"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get albums with no tracks
            cursor.execute(
                """
                SELECT id FROM albums 
                WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)
                AND id != 'unknown_album'
                """
            )
            orphaned_albums = [row["id"] for row in cursor.fetchall()]

            # Delete orphaned albums
            if orphaned_albums:
                albums_placeholders = ", ".join(["?"] * len(orphaned_albums))
                cursor.execute(
                    f"DELETE FROM albums WHERE id IN ({albums_placeholders})",
                    orphaned_albums,
                )
                deleted_albums = cursor.rowcount
            else:
                deleted_albums = 0

            # Get artists with no tracks or albums
            cursor.execute(
                """
                SELECT id FROM artists 
                WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks)
                AND id NOT IN (SELECT DISTINCT artist_id FROM albums)
                AND id != 'unknown_artist'
                """
            )
            orphaned_artists = [row["id"] for row in cursor.fetchall()]

            # Delete orphaned artists
            if orphaned_artists:
                artists_placeholders = ", ".join(["?"] * len(orphaned_artists))
                cursor.execute(
                    f"DELETE FROM artists WHERE id IN ({artists_placeholders})",
                    orphaned_artists,
                )
                deleted_artists = cursor.rowcount
            else:
                deleted_artists = 0

            conn.commit()
            return deleted_albums, deleted_artists
        finally:
            conn.close()

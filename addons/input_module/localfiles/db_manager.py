import os
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import time

logger = logging.getLogger(__name__.split(".")[-1])


class DbManager:
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

    def get_tracks_by_ids(self, track_ids: List[str]) -> List[Dict]:
        """Get track information by IDs"""
        if not track_ids:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in track_ids)
            cursor.execute(
                f"""
                SELECT t.*, a.title as album_title, ar.name as artist_name 
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.id IN ({placeholders})
            """,
                track_ids,
            )
            return [dict(row) for row in cursor.fetchall()]
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

    def search_tracks(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search tracks by query"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            search_term = f"%{query}%"

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.title LIKE ? OR a.title LIKE ? OR ar.name LIKE ?
            """,
                (search_term, search_term, search_term),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.title LIKE ? OR a.title LIKE ? OR ar.name LIKE ?
                ORDER BY t.title
                LIMIT ? OFFSET ?
            """,
                (search_term, search_term, search_term, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def search_albums(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search albums by query"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            search_term = f"%{query}%"

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.title LIKE ? OR ar.name LIKE ?
            """,
                (search_term, search_term),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.title LIKE ? OR ar.name LIKE ?
                ORDER BY a.title
                LIMIT ? OFFSET ?
            """,
                (search_term, search_term, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def search_artists(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search artists by query"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            search_term = f"%{query}%"

            # Get total count
            cursor.execute(
                "SELECT COUNT(*) as count FROM artists WHERE name LIKE ?",
                (search_term,),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT * FROM artists
                WHERE name LIKE ?
                ORDER BY name
                LIMIT ? OFFSET ?
            """,
                (search_term, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_all_albums(
        self, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get all albums"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute("SELECT COUNT(*) as count FROM albums")
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                ORDER BY a.title
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_all_artists(
        self, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get all artists"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute("SELECT COUNT(*) as count FROM artists")
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT * FROM artists
                ORDER BY name
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_recently_added_tracks(
        self, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get recently added tracks"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute("SELECT COUNT(*) as count FROM tracks")
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                ORDER BY t.last_updated DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_album_tracks(
        self, album_id: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get tracks for an album"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                "SELECT COUNT(*) as count FROM tracks WHERE album_id = ?", (album_id,)
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.album_id = ?
                ORDER BY t.track_number, t.title
                LIMIT ? OFFSET ?
            """,
                (album_id, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_artist_albums(
        self, artist_id: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get albums for an artist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                "SELECT COUNT(*) as count FROM albums WHERE artist_id = ?", (artist_id,)
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.artist_id = ?
                ORDER BY a.title
                LIMIT ? OFFSET ?
            """,
                (artist_id, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_non_enriched_artists(self, limit: int = 50) -> List[Dict]:
        """Get artists that haven't been enriched yet"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM artists
                WHERE enriched = 0 AND id != 'unknown_artist'
                LIMIT ?
            """,
                (limit,),
            )

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_non_enriched_albums(self, limit: int = 50) -> List[Dict]:
        """Get albums that haven't been enriched yet"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.enriched = 0 AND a.id != 'unknown_album'
                LIMIT ?
            """,
                (limit,),
            )

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_non_enriched_tracks(self, limit: int = 50) -> List[Dict]:
        """Get tracks that haven't been enriched yet"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
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

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # def get_artist_by_name(self, name: str) -> Optional[Dict]:
    #     """Get artist information by name (exact match)"""
    #     conn = self._get_connection()
    #     try:
    #         cursor = conn.cursor()
    #         cursor.execute("SELECT * FROM artists WHERE name = ? LIMIT 1", (name,))
    #         row = cursor.fetchone()
    #         return dict(row) if row else None
    #     finally:
    #         conn.close()

    def get_artist_by_mbid(self, mbid: str) -> Optional[Dict]:
        """Get artist information by MusicBrainz ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM artists WHERE mbid = ? LIMIT 1", (mbid,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_album_by_mbid(self, mbid: str) -> Optional[Dict]:
        """Get album information by MusicBrainz ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.mbid = ? LIMIT 1
                """,
                (mbid,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_album_by_title_and_artist(
        self, title: str, artist_id: str
    ) -> Optional[Dict]:
        """Get album information by title and artist ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.title = ? AND a.artist_id = ? LIMIT 1
                """,
                (title, artist_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_artist(self, artist_id: str, data: Dict[str, Any]) -> None:
        """Update artist information"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get artist table column names
            cursor.execute("PRAGMA table_info(artists)")
            valid_columns = {row["name"] for row in cursor.fetchall()}

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
            cursor.execute(query, values)
            conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for artist {artist_id}: {', '.join(filtered_out)}"
                )

        finally:
            conn.close()

    def update_album(self, album_id: str, data: Dict[str, Any]) -> None:
        """Update album information"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get album table column names
            cursor.execute("PRAGMA table_info(albums)")
            valid_columns = {row["name"] for row in cursor.fetchall()}

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
            cursor.execute(query, values)
            conn.commit()

            # Log if any fields were filtered out
            filtered_out = set(data.keys()) - valid_columns
            if filtered_out:
                logger.debug(
                    f"Filtered out non-existent columns for album {album_id}: {', '.join(filtered_out)}"
                )

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

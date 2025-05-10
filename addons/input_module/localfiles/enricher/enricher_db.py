import os
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import time

logger = logging.getLogger(__name__.split(".")[-1])


class EnricherDb:
    """
    Database manager specifically for the metadata enricher.
    Handles operations required for enrichment of music metadata.
    """

    def __init__(self, config):
        self.db_path = config["db_path"]
        self.artwork_path = config["artwork_path"]

    def _get_connection(self):
        """Get a database connection with row factory"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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

    def search_artists(self, query: str, limit: int = 50) -> Tuple[List[Dict], int]:
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
                LIMIT ?
            """,
                (search_term, limit),
            )

            return [dict(row) for row in cursor.fetchall()], total
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

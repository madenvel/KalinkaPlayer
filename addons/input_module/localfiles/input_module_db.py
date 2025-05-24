import os
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import time

logger = logging.getLogger(__name__.split(".")[-1])


class LocalFilesInputModuleDb:
    """
    Database manager specifically for the LocalFilesInputModule.
    Handles read-only operations for browsing and retrieving music.
    """

    def __init__(self, config):
        self.db_path = config["db_path"]
        self.artwork_path = config["artwork_path"]
        self.db_state = None

    def _get_connection(self):
        """Get a database connection with row factory"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_database_functional(self) -> Dict[str, Any]:
        """
        Check if the database exists and is properly structured.

        Returns:
            A dictionary with status information:
            {
                'exists': bool - Whether the database file exists
                'can_connect': bool - Whether we can connect to the database
                'has_tables': bool - Whether essential tables exist
                'functional': bool - Whether the database is fully functional
                'missing_tables': List[str] - List of essential tables that are missing (if any)
                'error': str - Error message (if any)
            }
        """
        result = {
            "exists": False,
            "can_connect": False,
            "has_tables": False,
            "functional": False,
            "missing_tables": [],
            "error": None,
        }

        # Check if database file exists
        if not os.path.isfile(self.db_path):
            result["error"] = f"Database file not found: {self.db_path}"
            return result

        result["exists"] = True

        # Check database connection
        try:
            conn = self._get_connection()
            result["can_connect"] = True
        except sqlite3.Error as e:
            result["error"] = f"Cannot connect to database: {str(e)}"
            return result

        # Check essential tables
        try:
            cursor = conn.cursor()

            # List of essential tables that should be present
            essential_tables = [
                "tracks",
                "albums",
                "artists",
                "playlists",
                "playlist_tracks",
            ]

            # Get list of tables in the database
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            existing_tables = {row["name"] for row in cursor.fetchall()}

            # Check if all essential tables exist
            for table in essential_tables:
                if table not in existing_tables:
                    result["missing_tables"].append(table)

            if not result["missing_tables"]:
                result["has_tables"] = True
            else:
                result["error"] = (
                    f"Missing tables: {', '.join(result['missing_tables'])}"
                )
        except sqlite3.Error as e:
            result["error"] = f"Error checking tables: {str(e)}"
            return result
        finally:
            conn.close()

        # Database is functional if it exists, can connect, and has all essential tables
        result["functional"] = (
            result["exists"] and result["can_connect"] and result["has_tables"]
        )

        return result

    def is_good(self) -> bool:
        """
        Check if the database is functional.
        Returns True if the database is functional, False otherwise.
        """
        if not os.path.isfile(self.db_path):
            self.db_state = None
            return False

        if not self.db_state or not self.db_state.get("functional", False):
            self.db_state = self._is_database_functional()

        return self.db_state.get("functional", False)

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
                ORDER BY a.last_updated DESC
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
                ORDER BY last_updated DESC
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
                ORDER BY COALESCE(t.disc_number, 1), t.track_number, t.title
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

    # Playlist related methods
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Dict]:
        """Get playlist information by ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_playlists(
        self, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get all playlists"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute("SELECT COUNT(*) as count FROM playlists")
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT * FROM playlists
                ORDER BY last_updated DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_playlist_tracks(
        self, playlist_id: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get tracks for a playlist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count 
                FROM playlist_tracks 
                WHERE playlist_id = ?
                """,
                (playlist_id,),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, ar.name as artist_name, pt.position
                FROM playlist_tracks pt
                JOIN tracks t ON pt.track_id = t.id
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE pt.playlist_id = ?
                ORDER BY pt.position
                LIMIT ? OFFSET ?
                """,
                (playlist_id, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_playlist_track_album_ids(
        self, playlist_id: str, limit: int = 4
    ) -> List[str]:
        """
        Get distinct album IDs for tracks in a playlist.
        Useful for generating playlist artwork.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT t.album_id
                FROM playlist_tracks pt
                JOIN tracks t ON pt.track_id = t.id
                WHERE pt.playlist_id = ?
                LIMIT ?
                """,
                (playlist_id, limit),
            )

            return [row["album_id"] for row in cursor.fetchall()]
        finally:
            conn.close()

    def search_playlists(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search playlists by name/description"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            search_term = f"%{query}%"

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM playlists
                WHERE name LIKE ? OR description LIKE ?
                """,
                (search_term, search_term),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT * FROM playlists
                WHERE name LIKE ? OR description LIKE ?
                ORDER BY name
                LIMIT ? OFFSET ?
                """,
                (search_term, search_term, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def create_playlist(
        self, playlist_id: str, name: str, description: str, created_by: str
    ) -> None:
        """Create a new playlist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            current_time = int(time.time())

            cursor.execute(
                """
                INSERT INTO playlists (
                    id, name, description, track_count, duration,
                    created_by, created_at, last_updated
                ) VALUES (?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (
                    playlist_id,
                    name,
                    description,
                    created_by,
                    current_time,
                    current_time,
                ),
            )

            conn.commit()
        except Exception as e:
            logger.error(f"Error creating playlist: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_playlist(self, playlist_id: str) -> bool:
        """Delete a playlist by ID. Returns True if successful."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Playlist tracks will be automatically deleted due to the ON DELETE CASCADE constraint
            cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
        except Exception as e:
            logger.error(f"Error deleting playlist: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """Update playlist information. Returns True if successful."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            current_time = int(time.time())

            # Build the SET clause with only provided fields
            fields = ["last_updated = ?"]
            values: List[Any] = [current_time]

            if name is not None:
                fields.append("name = ?")
                values.append(name)

            if description is not None:
                fields.append("description = ?")
                values.append(description)

            # Add playlist_id to the values
            values.append(playlist_id)

            query = f"UPDATE playlists SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(query, values)
            updated = cursor.rowcount > 0
            conn.commit()
            return updated
        except Exception as e:
            logger.error(f"Error updating playlist: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_tracks_to_playlist(
        self, playlist_id: str, track_ids: List[str], allow_duplicates: bool = False
    ) -> int:
        """
        Add tracks to a playlist.

        Args:
            playlist_id: ID of the playlist
            track_ids: List of track IDs to add
            allow_duplicates: Whether to allow duplicate tracks in the playlist

        Returns:
            Number of tracks added
        """
        if not track_ids:
            return 0

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            current_time = int(time.time())

            # Get the next position number
            if allow_duplicates:
                cursor.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                )
            else:
                # Remove any track IDs that are already in the playlist
                placeholders = ", ".join("?" for _ in track_ids)
                cursor.execute(
                    f"""
                    SELECT track_id FROM playlist_tracks 
                    WHERE playlist_id = ? AND track_id IN ({placeholders})
                    """,
                    [playlist_id] + track_ids,
                )
                existing_tracks = {row["track_id"] for row in cursor.fetchall()}
                track_ids = [
                    track_id
                    for track_id in track_ids
                    if track_id not in existing_tracks
                ]

                if not track_ids:
                    return 0

                # Get the next position number
                cursor.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                )

            next_pos = cursor.fetchone()["next_pos"]

            # Insert the tracks
            for i, track_id in enumerate(track_ids):
                cursor.execute(
                    """
                    INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (playlist_id, track_id, next_pos + i, current_time),
                )

            # Update the playlist's track count and duration
            self._update_playlist_stats(cursor, playlist_id)

            conn.commit()
            return len(track_ids)
        except Exception as e:
            logger.error(f"Error adding tracks to playlist: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_playlist_image(self, playlist_id: str, image_url: str) -> bool:
        """
        Update the image path for a playlist.

        Args:
            playlist_id: ID of the playlist
            image_url: Path to the playlist image

        Returns:
            True if the update was successful, False otherwise
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            current_time = int(time.time())

            cursor.execute(
                """
                UPDATE playlists SET
                image_url = ?,
                last_updated = ?
                WHERE id = ?
                """,
                (image_url, current_time, playlist_id),
            )

            updated = cursor.rowcount > 0
            conn.commit()
            return updated
        except Exception as e:
            logger.error(f"Error updating playlist image: {str(e)}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def remove_tracks_from_playlist(
        self, playlist_id: str, track_ids: List[str]
    ) -> int:
        """
        Remove tracks from a playlist.

        Args:
            playlist_id: ID of the playlist
            track_ids: List of track IDs to remove

        Returns:
            Number of tracks removed
        """
        if not track_ids:
            return 0

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Delete the tracks
            placeholders = ", ".join("?" for _ in track_ids)
            cursor.execute(
                f"""
                DELETE FROM playlist_tracks 
                WHERE playlist_id = ? AND track_id IN ({placeholders})
                """,
                [playlist_id] + track_ids,
            )

            removed_count = cursor.rowcount

            if removed_count > 0:
                # Reindex the remaining tracks to ensure positions are continuous
                cursor.execute(
                    """
                    SELECT track_id FROM playlist_tracks
                    WHERE playlist_id = ?
                    ORDER BY position
                    """,
                    (playlist_id,),
                )

                remaining_tracks = [row["track_id"] for row in cursor.fetchall()]

                # Delete all tracks
                cursor.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
                )

                # Re-insert with new positions
                current_time = int(time.time())
                for position, track_id in enumerate(remaining_tracks):
                    cursor.execute(
                        """
                        INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (playlist_id, track_id, position, current_time),
                    )

                # Update the playlist's track count and duration
                self._update_playlist_stats(cursor, playlist_id)

            conn.commit()
            return removed_count
        except Exception as e:
            logger.error(f"Error removing tracks from playlist: {str(e)}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def _update_playlist_stats(self, cursor, playlist_id: str) -> None:
        """Update playlist statistics (track count and duration)"""
        cursor.execute(
            """
            UPDATE playlists SET
            track_count = (
                SELECT COUNT(*) 
                FROM playlist_tracks 
                WHERE playlist_id = ?
            ),
            duration = (
                SELECT COALESCE(SUM(t.duration), 0)
                FROM playlist_tracks pt
                JOIN tracks t ON pt.track_id = t.id
                WHERE pt.playlist_id = ?
            ),
            last_updated = ?
            WHERE id = ?
            """,
            (playlist_id, playlist_id, int(time.time()), playlist_id),
        )

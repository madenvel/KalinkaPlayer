import os
import sqlite3
import uuid
import logging
import datetime
from enum import Enum
from typing import List, Optional, Set, Dict

from data_model.datamodel import (
    Track,
    Album,
    Artist,
    AlbumImage,
    ArtistImage,
    Label,
    Genre,
)

logger = logging.getLogger(__name__)


class EnrichmentState(str, Enum):
    """Enum representing the enrichment state of a track."""

    COMPLETE = "complete"
    FAILED = "failed"
    SOME = "some"


class FileDb:
    def __init__(self, db_path: str):
        """Initialize FileDb with database path and create tables if they don't exist.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._create_tables_if_not_exist()

    def _get_connection(self):
        """Get SQLite connection with row factory set to return dictionaries"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables_if_not_exist(self):
        """Create database tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create artists table
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS artists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            image_small TEXT,
            image_thumbnail TEXT,
            image_large TEXT,
            album_count INTEGER
        )
        """
        )

        # Create albums table
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS albums (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            duration INTEGER,
            track_count INTEGER,
            image_small TEXT,
            image_thumbnail TEXT,
            image_large TEXT,
            label_id TEXT,
            label_name TEXT,
            genre_id TEXT,
            genre_name TEXT,
            artist_id TEXT,
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
            duration INTEGER NOT NULL,
            performer_id TEXT,
            album_id TEXT NOT NULL,
            replaygain_peak REAL,
            replaygain_gain REAL,
            playlist_track_id TEXT,
            musicbrainz_id TEXT,
            file_update_ts TEXT,
            enrichment_state TEXT,
            enrichment_ts TEXT,
            FOREIGN KEY (performer_id) REFERENCES artists (id),
            FOREIGN KEY (album_id) REFERENCES albums (id)
        )
        """
        )

        # Create indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_musicbrainz_id ON tracks(musicbrainz_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id)"
        )

        # Insert default "unknown" entries if they don't exist
        self._insert_default_entries(cursor)

        conn.commit()
        conn.close()

    def _insert_default_entries(self, cursor):
        """Insert default unknown album and artist entries."""
        # Check if unknown artist exists, if not create it
        cursor.execute("SELECT * FROM artists WHERE id = ?", ("unknown_artist",))
        if cursor.fetchone() is None:
            cursor.execute(
                """
            INSERT INTO artists (id, name, album_count)
            VALUES (?, ?, ?)
            """,
                ("unknown_artist", "Unknown Artist", 0),
            )

        # Check if unknown album exists, if not create it
        cursor.execute("SELECT * FROM albums WHERE id = ?", ("unknown_album",))
        if cursor.fetchone() is None:
            cursor.execute(
                """
            INSERT INTO albums (id, title, artist_id)
            VALUES (?, ?, ?)
            """,
                ("unknown_album", "Unknown Album", "unknown_artist"),
            )

    def get_tracks_by_album(
        self, album_id: str, offset: int = 0, limit: int = 50
    ) -> List[Track]:
        """Get all tracks of an album.

        Args:
            album_id: Album ID to get tracks for
            offset: Number of tracks to skip
            limit: Maximum number of tracks to return

        Returns:
            List of Track objects
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
        SELECT t.*, 
               a.id as album_id, a.title as album_title, a.duration as album_duration, 
               a.track_count as album_track_count, a.image_small as album_image_small, 
               a.image_thumbnail as album_image_thumbnail, a.image_large as album_image_large,
               a.label_id, a.label_name, a.genre_id, a.genre_name,
               p.id as performer_id, p.name as performer_name, 
               p.image_small as performer_image_small, p.image_thumbnail as performer_image_thumbnail, 
               p.image_large as performer_image_large,
               art.id as artist_id, art.name as artist_name
        FROM tracks t
        JOIN albums a ON t.album_id = a.id
        LEFT JOIN artists p ON t.performer_id = p.id
        LEFT JOIN artists art ON a.artist_id = art.id
        WHERE t.album_id = ?
        ORDER BY t.title
        LIMIT ? OFFSET ?
        """,
            (album_id, limit, offset),
        )

        return [self._row_to_track(row) for row in cursor.fetchall()]

    def get_albums_by_artist(
        self, artist_id: str, offset: int = 0, limit: int = 50
    ) -> List[Album]:
        """Get all albums by an artist.

        Args:
            artist_id: Artist ID to get albums for
            offset: Number of albums to skip
            limit: Maximum number of albums to return

        Returns:
            List of Album objects
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
        SELECT a.*,
               art.id as artist_id, art.name as artist_name, 
               art.image_small as artist_image_small, art.image_thumbnail as artist_image_thumbnail, 
               art.image_large as artist_image_large, art.album_count as artist_album_count
        FROM albums a
        LEFT JOIN artists art ON a.artist_id = art.id
        WHERE a.artist_id = ?
        ORDER BY a.title
        LIMIT ? OFFSET ?
        """,
            (artist_id, limit, offset),
        )

        return [self._row_to_album(row) for row in cursor.fetchall()]

    def get_track(self, track_id: str) -> Optional[Track]:
        """Get track metadata by ID.

        Args:
            track_id: Track ID

        Returns:
            Track object or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
        SELECT t.*, 
               a.id as album_id, a.title as album_title, a.duration as album_duration, 
               a.track_count as album_track_count, a.image_small as album_image_small, 
               a.image_thumbnail as album_image_thumbnail, a.image_large as album_image_large,
               a.label_id, a.label_name, a.genre_id, a.genre_name,
               p.id as performer_id, p.name as performer_name, 
               p.image_small as performer_image_small, p.image_thumbnail as performer_image_thumbnail, 
               p.image_large as performer_image_large,
               art.id as artist_id, art.name as artist_name
        FROM tracks t
        JOIN albums a ON t.album_id = a.id
        LEFT JOIN artists p ON t.performer_id = p.id
        LEFT JOIN artists art ON a.artist_id = art.id
        WHERE t.id = ?
        """,
            (track_id,),
        )

        row = cursor.fetchone()
        return self._row_to_track(row) if row else None

    def get_album(self, album_id: str) -> Optional[Album]:
        """Get album metadata by ID.

        Args:
            album_id: Album ID

        Returns:
            Album object or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
        SELECT a.*,
               art.id as artist_id, art.name as artist_name, 
               art.image_small as artist_image_small, art.image_thumbnail as artist_image_thumbnail, 
               art.image_large as artist_image_large, art.album_count as artist_album_count
        FROM albums a
        LEFT JOIN artists art ON a.artist_id = art.id
        WHERE a.id = ?
        """,
            (album_id,),
        )

        row = cursor.fetchone()
        return self._row_to_album(row) if row else None

    def get_artist(self, artist_id: str) -> Optional[Artist]:
        """Get artist metadata by ID.

        Args:
            artist_id: Artist ID

        Returns:
            Artist object or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))

        row = cursor.fetchone()
        return self._row_to_artist(row) if row else None

    def add_or_update_track(
        self,
        track: Track,
        musicbrainz_id: str = None,
        enrichment_state: str = None,
        file_update_ts: str = None,
    ) -> int:
        """Add a track to the database or update it if it exists.

        Args:
            track: Track object
            musicbrainz_id: Optional musicbrainz ID for the track
            enrichment_state: Optional enrichment state
            file_update_ts: Optional file update timestamp (falls back to current time if not provided)
                            If this timestamp is newer than the existing one, all fields will be updated.
        Returns:
            The number of entries added or updated (0 if no changes made)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Ensure album and artist exist
        album_id = track.album.id if track.album else "unknown_album"
        performer_id = track.performer.id if track.performer else "unknown_artist"

        # Use provided file_update_ts or current time
        now = datetime.datetime.now().isoformat()
        file_update_ts = file_update_ts or now

        try:
            # Check if the track already exists
            cursor.execute("SELECT * FROM tracks WHERE id = ?", (track.id,))
            existing_track = cursor.fetchone()

            if not existing_track:
                # Track doesn't exist, insert it
                cursor.execute(
                    """
                INSERT INTO tracks (
                    id, title, duration, performer_id, album_id,
                    replaygain_peak, replaygain_gain, playlist_track_id,
                    musicbrainz_id, file_update_ts, enrichment_state, enrichment_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        track.id,
                        track.title,
                        track.duration,
                        performer_id,
                        album_id,
                        track.replaygain_peak,
                        track.replaygain_gain,
                        track.playlist_track_id,
                        musicbrainz_id,
                        file_update_ts,
                        enrichment_state,
                        now if enrichment_state else None,
                    ),
                )
                rows_count = cursor.rowcount
                conn.commit()
                return rows_count
            else:
                # Track exists, check file_update_ts to decide whether to update
                existing_ts = existing_track["file_update_ts"]

                # Only update if the existing timestamp is None or if the new timestamp is newer
                if existing_ts is None or (
                    file_update_ts is not None and file_update_ts > existing_ts
                ):
                    # Update all track fields
                    cursor.execute(
                        """
                    UPDATE tracks SET
                        title = ?, duration = ?, performer_id = ?, album_id = ?,
                        replaygain_peak = ?, replaygain_gain = ?, playlist_track_id = ?,
                        file_update_ts = ?
                    WHERE id = ?
                    """,
                        (
                            track.title,
                            track.duration,
                            performer_id,
                            album_id,
                            track.replaygain_peak,
                            track.replaygain_gain,
                            track.playlist_track_id,
                            file_update_ts,
                            track.id,
                        ),
                    )

                    # Also update enrichment data if provided
                    if enrichment_state is not None or musicbrainz_id is not None:
                        # Only include fields that are provided
                        update_fields = []
                        update_values = []

                        if musicbrainz_id is not None:
                            update_fields.append("musicbrainz_id = ?")
                            update_values.append(musicbrainz_id)

                        if enrichment_state is not None:
                            update_fields.append("enrichment_state = ?")
                            update_values.append(enrichment_state)
                            update_fields.append("enrichment_ts = ?")
                            update_values.append(now)

                        if update_fields:
                            query = f"UPDATE tracks SET {', '.join(update_fields)} WHERE id = ?"
                            update_values.append(track.id)
                            cursor.execute(query, tuple(update_values))

                    rows_count = cursor.rowcount
                    conn.commit()
                    return rows_count
                else:
                    # The existing record is newer or same age, don't update
                    return 0

        except sqlite3.Error as e:
            conn.rollback()
            logger.error(f"Error adding or updating track in database: {e}")
            return 0
        finally:
            conn.close()

    def add_or_enrich_artist(self, artist: Artist, enrich_only: bool = False) -> bool:
        """Add a new artist to the database or enrich an existing one.

        If the artist doesn't exist yet, it will be created.
        If the artist already exists:
        - With enrich_only=True: only update fields that are None in the database
        - With enrich_only=False: replace all fields with the new values

        Args:
            artist: Artist object with artist data
            enrich_only: If True, only update fields that are None in the database
                         If False, replace all fields with new values

        Returns:
            bool: True if the artist was created or updated, False otherwise
        """
        if not artist or not artist.id:
            return False

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Check if artist exists
            cursor.execute("SELECT * FROM artists WHERE id = ?", (artist.id,))
            existing = cursor.fetchone()

            # If artist doesn't exist, create it
            if not existing:
                # Insert the artist
                cursor.execute(
                    """
                INSERT INTO artists (
                    id, name, image_small, image_thumbnail, image_large, album_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        artist.id,
                        artist.name,
                        artist.image.small if artist.image else None,
                        artist.image.thumbnail if artist.image else None,
                        artist.image.large if artist.image else None,
                        artist.album_count,
                    ),
                )
                conn.commit()
                return True

            # Artist exists, update it based on enrich_only flag
            if enrich_only:
                # Build update query dynamically based on fields that are None in DB but set in artist
                fields_to_update = []
                values = []

                # Helper to check if a field should be updated
                def should_update(db_field, new_value):
                    return existing[db_field] is None and new_value is not None

                # Handle basic fields
                if should_update("name", artist.name):
                    fields_to_update.append("name = ?")
                    values.append(artist.name)

                if should_update("album_count", artist.album_count):
                    fields_to_update.append("album_count = ?")
                    values.append(artist.album_count)

                # Handle images
                if artist.image:
                    if should_update("image_small", artist.image.small):
                        fields_to_update.append("image_small = ?")
                        values.append(artist.image.small)

                    if should_update("image_thumbnail", artist.image.thumbnail):
                        fields_to_update.append("image_thumbnail = ?")
                        values.append(artist.image.thumbnail)

                    if should_update("image_large", artist.image.large):
                        fields_to_update.append("image_large = ?")
                        values.append(artist.image.large)

                # If no fields to update, return False
                if not fields_to_update:
                    return False

                # Build and execute the update query
                query = f"UPDATE artists SET {', '.join(fields_to_update)} WHERE id = ?"
                values.append(artist.id)  # Add artist ID for the WHERE clause
            else:
                # Replace all fields with new values
                query = """
                UPDATE artists SET
                    name = ?, image_small = ?, image_thumbnail = ?, image_large = ?, album_count = ?
                WHERE id = ?
                """
                values = [
                    artist.name,
                    artist.image.small if artist.image else None,
                    artist.image.thumbnail if artist.image else None,
                    artist.image.large if artist.image else None,
                    artist.album_count,
                    artist.id,
                ]

            cursor.execute(query, tuple(values))
            conn.commit()

            # Return True if any rows were affected
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            conn.rollback()
            logger.error(f"Error adding or enriching artist in database: {e}")
            return False
        finally:
            conn.close()

    def add_or_enrich_album(self, album: Album, enrich_only: bool = False) -> bool:
        """Add a new album to the database or enrich an existing one.

        If the album doesn't exist yet, it will be created.
        If the album already exists:
        - With enrich_only=True: only update fields that are None in the database
        - With enrich_only=False: replace all fields with the new values

        Args:
            album: Album object with album data
            enrich_only: If True, only update fields that are None in the database
                         If False, replace all fields with new values

        Returns:
            bool: True if the album was created or updated, False otherwise
        """
        if not album or not album.id:
            return False

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Check if album exists
            cursor.execute("SELECT * FROM albums WHERE id = ?", (album.id,))
            existing = cursor.fetchone()

            # If album doesn't exist, create it
            if not existing:
                # Ensure album artist exists
                artist_id = album.artist.id if album.artist else "unknown_artist"

                # Add label and genre fields
                label_id = None
                label_name = None
                if album.label:
                    label_id = album.label.id
                    label_name = album.label.name

                genre_id = None
                genre_name = None
                if album.genre:
                    genre_id = album.genre.id
                    genre_name = album.genre.name

                # Insert the album
                cursor.execute(
                    """
                INSERT INTO albums (
                    id, title, duration, track_count,
                    image_small, image_thumbnail, image_large,
                    label_id, label_name, genre_id, genre_name, artist_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        album.id,
                        album.title,
                        album.duration,
                        album.track_count,
                        album.image.small if album.image else None,
                        album.image.thumbnail if album.image else None,
                        album.image.large if album.image else None,
                        label_id,
                        label_name,
                        genre_id,
                        genre_name,
                        artist_id,
                    ),
                )
                conn.commit()
                return True

            # Album exists, update it based on enrich_only flag
            if enrich_only:
                # Build update query dynamically based on fields that are None in DB but set in album
                fields_to_update = []
                values = []

                # Helper to check if a field should be updated
                def should_update(db_field, new_value):
                    return existing[db_field] is None and new_value is not None

                # Handle basic fields
                if should_update("title", album.title):
                    fields_to_update.append("title = ?")
                    values.append(album.title)

                if should_update("duration", album.duration):
                    fields_to_update.append("duration = ?")
                    values.append(album.duration)

                if should_update("track_count", album.track_count):
                    fields_to_update.append("track_count = ?")
                    values.append(album.track_count)

                # Handle images
                if album.image:
                    if should_update("image_small", album.image.small):
                        fields_to_update.append("image_small = ?")
                        values.append(album.image.small)

                    if should_update("image_thumbnail", album.image.thumbnail):
                        fields_to_update.append("image_thumbnail = ?")
                        values.append(album.image.thumbnail)

                    if should_update("image_large", album.image.large):
                        fields_to_update.append("image_large = ?")
                        values.append(album.image.large)

                # Handle label
                if album.label:
                    if should_update("label_id", album.label.id):
                        fields_to_update.append("label_id = ?")
                        values.append(album.label.id)

                    if should_update("label_name", album.label.name):
                        fields_to_update.append("label_name = ?")
                        values.append(album.label.name)

                # Handle genre
                if album.genre:
                    if should_update("genre_id", album.genre.id):
                        fields_to_update.append("genre_id = ?")
                        values.append(album.genre.id)

                    if should_update("genre_name", album.genre.name):
                        fields_to_update.append("genre_name = ?")
                        values.append(album.genre.name)

                # Handle artist reference
                if album.artist and should_update("artist_id", album.artist.id):
                    fields_to_update.append("artist_id = ?")
                    values.append(album.artist.id)

                # If no fields to update, return False
                if not fields_to_update:
                    return False

                # Build and execute the update query
                query = f"UPDATE albums SET {', '.join(fields_to_update)} WHERE id = ?"
                values.append(album.id)  # Add album ID for the WHERE clause
            else:
                # Replace all fields with new values
                artist_id = album.artist.id if album.artist else "unknown_artist"

                # Add label and genre fields
                label_id = None
                label_name = None
                if album.label:
                    label_id = album.label.id
                    label_name = album.label.name

                genre_id = None
                genre_name = None
                if album.genre:
                    genre_id = album.genre.id
                    genre_name = album.genre.name

                query = """
                UPDATE albums SET
                    title = ?, duration = ?, track_count = ?,
                    image_small = ?, image_thumbnail = ?, image_large = ?,
                    label_id = ?, label_name = ?, genre_id = ?, genre_name = ?, artist_id = ?
                WHERE id = ?
                """
                values = [
                    album.title,
                    album.duration,
                    album.track_count,
                    album.image.small if album.image else None,
                    album.image.thumbnail if album.image else None,
                    album.image.large if album.image else None,
                    label_id,
                    label_name,
                    genre_id,
                    genre_name,
                    artist_id,
                    album.id,
                ]

            cursor.execute(query, tuple(values))
            conn.commit()

            # Return True if any rows were affected
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            conn.rollback()
            logger.error(f"Error adding or enriching album in database: {e}")
            return False
        finally:
            conn.close()

    def add_album(self, album: Album) -> None:
        """Add an album to the database.

        Deprecated: Use add_or_enrich_album() instead.

        Args:
            album: Album object
        """
        self.add_or_enrich_album(album, enrich_only=False)

    def enrich_album(self, album: Album) -> bool:
        """Update album fields in the database that are None with values from the provided Album.

        Deprecated: Use add_or_enrich_album(album, enrich_only=True) instead.

        Args:
            album: Album object with potentially new information

        Returns:
            bool: True if any fields were updated, False otherwise
        """
        return self.add_or_enrich_album(album, enrich_only=True)

    def update_track(
        self,
        track_id: str,
        track: Optional[Track] = None,
        enrichment_state: Optional[str] = None,
        enrichment_ts: Optional[str] = None,
    ) -> None:
        """Update track metadata.

        Args:
            track_id: ID of the track to update
            track: Optional Track object with updated data
            enrichment_state: Optional enrichment state
            enrichment_ts: Optional enrichment timestamp (falls back to current time if enrichment_state is provided)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get existing track to preserve fields not in Track model
        cursor.execute(
            """
        SELECT *
        FROM tracks WHERE id = ?
        """,
            (track_id,),
        )

        existing_track = cursor.fetchone()
        if not existing_track:
            # Track doesn't exist, nothing to update
            conn.close()
            return

        # Update track data if provided
        if track:
            album_id = track.album.id if track.album else "unknown_album"
            performer_id = track.performer.id if track.performer else None

            cursor.execute(
                """
            UPDATE tracks SET
                title = ?, duration = ?, performer_id = ?, album_id = ?,
                replaygain_peak = ?, replaygain_gain = ?, playlist_track_id = ?
            WHERE id = ?
            """,
                (
                    track.title,
                    track.duration,
                    performer_id,
                    album_id,
                    track.replaygain_peak,
                    track.replaygain_gain,
                    track.playlist_track_id,
                    track_id,
                ),
            )

        # Update enrichment info if provided
        if enrichment_state is not None:
            # Use provided timestamp or current time
            timestamp = enrichment_ts
            if timestamp is None:
                timestamp = datetime.datetime.now().isoformat()

            cursor.execute(
                """
            UPDATE tracks SET
                enrichment_state = ?, enrichment_ts = ?
            WHERE id = ?
            """,
                (enrichment_state, timestamp, track_id),
            )

        conn.commit()
        conn.close()

    def delete_track(self, track_id: str) -> bool:
        """Delete a track from the database.

        Args:
            track_id: ID of the track to delete

        Returns:
            True if the track was deleted, False if it didn't exist
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        deleted = cursor.rowcount > 0

        conn.commit()
        conn.close()

        return deleted

    def get_tracks_not_in_list(self, track_ids: List[str]) -> List[str]:
        """Get track IDs that are present in the database but not in the provided list.

        Args:
            track_ids: List of track IDs to check against

        Returns:
            List of track IDs that are in the database but not in the provided list
        """
        # Handle empty list case efficiently
        if not track_ids:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM tracks")

                # Return all track IDs
                return [row["id"] for row in cursor]
            finally:
                conn.close()

        # Process in chunks to avoid SQLite limitations with large parameter lists
        chunk_size = 500
        track_id_chunks = [
            track_ids[i : i + chunk_size] for i in range(0, len(track_ids), chunk_size)
        ]

        conn = self._get_connection()
        missing_ids = []
        try:
            cursor = conn.cursor()

            # Use a temporary table for efficient comparison
            cursor.execute(
                "CREATE TEMPORARY TABLE IF NOT EXISTS temp_track_ids (id TEXT PRIMARY KEY)"
            )
            cursor.execute("DELETE FROM temp_track_ids")

            # Insert track ID chunks into the temporary table
            for chunk in track_id_chunks:
                cursor.executemany(
                    "INSERT OR IGNORE INTO temp_track_ids VALUES (?)",
                    [(id,) for id in chunk],
                )

            # Query for tracks not in the provided list
            cursor.execute(
                """
                SELECT t.id FROM tracks t
                LEFT JOIN temp_track_ids temp ON t.id = temp.id
                WHERE temp.id IS NULL
            """
            )

            # Collect all IDs
            missing_ids = [row["id"] for row in cursor]

            # Clean up
            cursor.execute("DROP TABLE IF EXISTS temp_track_ids")

        finally:
            conn.close()

        return missing_ids

    def get_tracks_needing_enrichment(self, limit: int = 100, offset: int = 0):
        """Get tracks that have no enrichment_state set or have it set to "failed".

        Returns a generator yielding Track IDs that need enrichment.

        Args:
            limit: Maximum number of tracks to return at once
            offset: Number of tracks to skip

        Returns:
            Generator yielding Track IDs that need enrichment
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
        SELECT id
        FROM tracks
        WHERE enrichment_state IS NULL OR enrichment_state = ?
        ORDER BY t.id
        LIMIT ? OFFSET ?
        """,
            (EnrichmentState.FAILED, limit, offset),
        )

        # Yield each track from the result set
        for row in cursor:
            yield self._row_to_track(row)

        conn.close()

    def _row_to_track(self, row: sqlite3.Row) -> Track:
        """Convert a database row to a Track object.

        Args:
            row: Database row

        Returns:
            Track object
        """
        if not row:
            return None

        # Create the album object with different column names from join query
        album = self._row_to_album(row, prefix="album_")

        # Create performer artist if exists
        performer = None
        if row["performer_id"]:
            performer_image = None
            if (
                row["performer_image_small"]
                or row["performer_image_thumbnail"]
                or row["performer_image_large"]
            ):
                performer_image = ArtistImage(
                    small=row["performer_image_small"],
                    thumbnail=row["performer_image_thumbnail"],
                    large=row["performer_image_large"],
                )

            performer = Artist(
                id=row["performer_id"],
                name=row["performer_name"],
                image=performer_image,
            )

        # Create the track
        return Track(
            id=row["id"],
            title=row["title"],
            duration=row["duration"],
            performer=performer,
            album=album,
            replaygain_peak=row["replaygain_peak"],
            replaygain_gain=row["replaygain_gain"],
            playlist_track_id=row["playlist_track_id"],
        )

    def _row_to_album(self, row: sqlite3.Row, prefix: str = "") -> Album:
        """Convert a database row to an Album object.

        Args:
            row: Database row
            prefix: Optional prefix for column names when used in joined queries

        Returns:
            Album object
        """
        if not row:
            return None

        # Handle column name prefixing for joined queries
        def col(name):
            return f"{prefix}{name}" if prefix else name

        album_id = row[col("id")] if not prefix else row["album_id"]
        album_title = row[col("title")] if not prefix else row["album_title"]
        album_duration = row.get(col("duration"))
        album_track_count = row.get(col("track_count"))

        # Create album image if exists
        album_image = None
        if (
            row.get(col("image_small"))
            or row.get(col("image_thumbnail"))
            or row.get(col("image_large"))
        ):
            album_image = AlbumImage(
                small=row.get(col("image_small")),
                thumbnail=row.get(col("image_thumbnail")),
                large=row.get(col("image_large")),
            )

        # Create label if exists
        label = None
        if row.get(col("label_id")):
            label = Label(id=row.get(col("label_id")), name=row.get(col("label_name")))

        # Create genre if exists
        genre = None
        if row.get(col("genre_id")):
            genre = Genre(id=row.get(col("genre_id")), name=row.get(col("genre_name")))

        # Create album artist if exists
        artist = None
        artist_id_col = "artist_id"  # This is consistently named in our queries

        if row.get(artist_id_col):
            artist_image = None
            if (
                row.get("artist_image_small")
                or row.get("artist_image_thumbnail")
                or row.get("artist_image_large")
            ):
                artist_image = ArtistImage(
                    small=row.get("artist_image_small"),
                    thumbnail=row.get("artist_image_thumbnail"),
                    large=row.get("artist_image_large"),
                )

            artist = Artist(
                id=row[artist_id_col],
                name=row["artist_name"],
                image=artist_image,
                album_count=row.get("artist_album_count"),
            )

        # Create the album
        return Album(
            id=album_id,
            title=album_title,
            duration=album_duration,
            track_count=album_track_count,
            image=album_image,
            label=label,
            genre=genre,
            artist=artist,
        )

    def _row_to_artist(self, row: sqlite3.Row) -> Artist:
        """Convert a database row to an Artist object.

        Args:
            row: Database row

        Returns:
            Artist object
        """
        if not row:
            return None

        # Create artist image if exists
        artist_image = None
        if row["image_small"] or row["image_thumbnail"] or row["image_large"]:
            artist_image = ArtistImage(
                small=row["image_small"],
                thumbnail=row["image_thumbnail"],
                large=row["image_large"],
            )

        # Create the artist
        return Artist(
            id=row["id"],
            name=row["name"],
            image=artist_image,
            album_count=row["album_count"],
        )

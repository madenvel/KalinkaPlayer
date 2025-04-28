import os
import sqlite3
import uuid
import logging
import datetime
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
        conn = sqlite3.connect(self.db_path)
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
            enrichment_timestamp TEXT,
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

    def add_track(
        self,
        track: Track,
        musicbrainz_id: str = None,
        enrichment_state: str = None,
        file_update_ts: str = None,
    ) -> None:
        """Add a track to the database.

        Args:
            track: Track object
            musicbrainz_id: Optional musicbrainz ID for the track
            enrichment_state: Optional enrichment state
            file_update_ts: Optional file update timestamp (falls back to current time if not provided)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Ensure album and artist exist
        album_id = track.album.id if track.album else "unknown_album"
        performer_id = track.performer.id if track.performer else None

        # Use provided file_update_ts or current time
        now = datetime.datetime.now().isoformat()
        file_update_ts = file_update_ts or now

        # Insert the track
        cursor.execute(
            """
        INSERT OR REPLACE INTO tracks (
            id, title, duration, performer_id, album_id,
            replaygain_peak, replaygain_gain, playlist_track_id,
            musicbrainz_id, file_update_ts, enrichment_state, enrichment_timestamp
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

        conn.commit()
        conn.close()

    def add_album(self, album: Album) -> None:
        """Add an album to the database.

        Args:
            album: Album object
        """
        conn = self._get_connection()
        cursor = conn.cursor()

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
        INSERT OR REPLACE INTO albums (
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
        conn.close()

    def add_artist(self, artist: Artist) -> None:
        """Add an artist to the database.

        Args:
            artist: Artist object
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Insert the artist
        cursor.execute(
            """
        INSERT OR REPLACE INTO artists (
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
        conn.close()

    def update_track(self, track: Track) -> None:
        """Update track metadata.

        Args:
            track: Track object with updated data
            file_update_ts: Optional file update timestamp (falls back to current time if not provided)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get existing track to preserve fields not in Track model
        cursor.execute(
            """
        SELECT musicbrainz_id, enrichment_state, enrichment_timestamp
        FROM tracks WHERE id = ?
        """,
            (track.id,),
        )

        existing_track = cursor.fetchone()
        if not existing_track:
            # Track doesn't exist, nothing to update
            conn.close()
            return

        # Update the track
        album_id = track.album.id if track.album else "unknown_album"
        performer_id = track.performer.id if track.performer else None

        cursor.execute(
            """
        UPDATE tracks SET
            title = ?, duration = ?, performer_id = ?, album_id = ?,
            replaygain_peak = ?, replaygain_gain = ?, playlist_track_id = ?,
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
                track.id,
            ),
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
        if not track_ids:
            # If no IDs provided, potentially return all tracks from DB
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tracks")
            result = [row["id"] for row in cursor.fetchall()]
            conn.close()
            return result

        conn = self._get_connection()
        cursor = conn.cursor()

        # Use a temporary table for large lists
        cursor.execute(
            "CREATE TEMPORARY TABLE IF NOT EXISTS temp_track_ids (id TEXT PRIMARY KEY)"
        )
        cursor.execute("DELETE FROM temp_track_ids")

        # Insert all IDs into the temporary table
        cursor.executemany(
            "INSERT INTO temp_track_ids VALUES (?)", [(id,) for id in track_ids]
        )

        # Find tracks that exist in the database but not in the provided list
        cursor.execute(
            """
        SELECT t.id FROM tracks t
        LEFT JOIN temp_track_ids temp ON t.id = temp.id
        WHERE temp.id IS NULL
        """
        )

        missing_ids = [row["id"] for row in cursor.fetchall()]

        # Clean up
        cursor.execute("DROP TABLE temp_track_ids")
        conn.close()

        return missing_ids

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

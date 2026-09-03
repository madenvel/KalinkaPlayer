import os
import re
import shutil
import sqlite3
import logging
from typing import List, Dict, Optional, Any, Tuple
import time
from pathlib import Path

from .config_model import LocalFilesConfig
from .utils.name_utils import fold_diacritics

logger = logging.getLogger(__name__.split(".")[-1])


_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


def fold_for_match(value: Optional[str]) -> str:
    """Normalise a string for accent/case/punctuation-insensitive ``LIKE``
    matching. Applied identically to BOTH the column (via the SQL ``fold``
    function) and the query term so the two sides normalise the same way:

      * diacritic-fold   — ``Oxygene`` finds ``Oxygène``
      * casefold         — Unicode case-insensitivity. SQLite's ``LIKE`` only
        folds ASCII, so without this a lowercase Cyrillic query
        (``гребенщиков``) never matches a capitalised name (``Гребенщиков``).
      * punctuation→space — ``Jean Michel Jarre`` matches ``Jean-Michel Jarre``.
    """
    folded = fold_diacritics(value or "").casefold()
    folded = _NON_WORD_RE.sub(" ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _sql_fold(value: Optional[str]) -> str:
    """SQLite-callable match-fold (see :func:`fold_for_match`). Registered as a
    deterministic ``fold`` function on every connection."""
    return fold_for_match(value)


class LocalFilesInputModuleDb:
    """
    Database manager specifically for the LocalFilesInputModule.
    Handles read-only operations for browsing and retrieving music.
    """

    def __init__(self, config: LocalFilesConfig):
        self.db_path = Path(config.db_path).expanduser().resolve()
        self.artwork_path = Path(config.artwork_path).expanduser().resolve()
        self.db_state = None

    def purge_all(self):
        """Delete the database (with its WAL/-shm sidecars) and the artwork
        cache so the index is rebuilt from scratch.

        Used by the "Rebuild library on next restart" one-shot. Call before
        any worker opens the DB (i.e. early in setup), so nothing recreates
        the file mid-purge.
        """
        self._purge_database()
        self._purge_artwork()

    def _purge_database(self):
        """Remove the database file and its WAL/-shm sidecars."""
        removed = False
        for sidecar in ("", "-wal", "-shm"):
            path = (
                self.db_path
                if not sidecar
                else self.db_path.with_name(self.db_path.name + sidecar)
            )
            if path.exists():
                try:
                    path.unlink()
                    removed = True
                    logger.info(f"Removed {path}")
                except OSError as e:
                    logger.error(f"Failed to remove {path}: {e}")
        if not removed:
            logger.info("No existing database to purge.")

    def _purge_artwork(self):
        """Purge the artwork directory by removing all files and folders."""
        if self.artwork_path.exists() and self.artwork_path.is_dir():
            try:
                shutil.rmtree(self.artwork_path)
                logger.info(f"Artwork directory purged: {self.artwork_path}")
            except OSError as e:
                logger.error(f"Failed to purge artwork directory: {e}")
        else:
            logger.info("No existing artwork directory to purge.")

    def _get_connection(self):
        """Get a database connection with row factory"""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.create_function("fold", 1, _sql_fold, deterministic=True)
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
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name 
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

    def get_root_signature(self, root: str) -> Optional[str]:
        """The mount identity the indexer recorded for a music root, or None.
        Lets the playback path tell an unmounted static share (identity
        changed) from a genuinely missing file."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM indexer_state WHERE key = ?",
                (f"root_signature:{root}",),
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def get_tracks_by_ids(self, track_ids: List[str]) -> List[Dict]:
        """Get track information by IDs, preserving the order of track_ids."""
        if not track_ids:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in track_ids)
            cursor.execute(
                f"""
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
                FROM tracks t
                LEFT JOIN albums a ON t.album_id = a.id
                LEFT JOIN artists ar ON t.artist_id = ar.id
                WHERE t.id IN ({placeholders})
            """,
                track_ids,
            )
            rows_by_id = {row["id"]: dict(row) for row in cursor.fetchall()}
            return [rows_by_id[tid] for tid in track_ids if tid in rows_by_id]
        finally:
            conn.close()

    def get_albums_by_ids(self, album_ids: List[str]) -> List[Dict]:
        """Get album information by IDs, preserving the order of album_ids."""
        if not album_ids:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in album_ids)
            cursor.execute(
                f"""
                SELECT a.*, ar.name as artist_name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE a.id IN ({placeholders})
                """,
                album_ids,
            )
            rows_by_id = {row["id"]: dict(row) for row in cursor.fetchall()}
            return [rows_by_id[aid] for aid in album_ids if aid in rows_by_id]
        finally:
            conn.close()

    def get_artists_by_ids(self, artist_ids: List[str]) -> List[Dict]:
        """Get artist information by IDs, preserving the order of artist_ids."""
        if not artist_ids:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in artist_ids)
            cursor.execute(
                f"SELECT * FROM artists WHERE id IN ({placeholders})",
                artist_ids,
            )
            rows_by_id = {row["id"]: dict(row) for row in cursor.fetchall()}
            return [rows_by_id[aid] for aid in artist_ids if aid in rows_by_id]
        finally:
            conn.close()

    def get_playlists_by_ids(self, playlist_ids: List[str]) -> List[Dict]:
        """Get playlist information by IDs, preserving the order of playlist_ids."""
        if not playlist_ids:
            return []

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in playlist_ids)
            cursor.execute(
                f"SELECT * FROM playlists WHERE id IN ({placeholders})",
                playlist_ids,
            )
            rows_by_id = {row["id"]: dict(row) for row in cursor.fetchall()}
            return [rows_by_id[pid] for pid in playlist_ids if pid in rows_by_id]
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

    @staticmethod
    def _token_where(query: str, fields: Tuple[str, ...]) -> Tuple[str, List[str]]:
        """WHERE clause requiring every folded query token to match at least
        one of ``fields`` (fold()-wrapped SQL expressions).

        Per-token matching lets a query span fields — "come together beatles"
        is title + artist, which a single whole-query LIKE can never match.
        A single-token query behaves exactly like the old whole-string scan."""
        tokens = fold_for_match(query).split()
        if not tokens:
            # Preserve the old empty-query behavior: match everything.
            return "1=1", []
        clause = " AND ".join(
            "(" + " OR ".join(f"{f} LIKE ?" for f in fields) + ")"
            for _ in tokens
        )
        params = [f"%{t}%" for t in tokens for _ in fields]
        return clause, params

    def search_tracks(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search tracks by query (every token must match title/album/artist)"""
        where, params = self._token_where(
            query, ("fold(t.title)", "fold(a.title)", "fold(ar.name)")
        )
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                f"""
                SELECT COUNT(*) as count FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE {where}
            """,
                params,
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                f"""
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE {where}
                ORDER BY t.title
                LIMIT ? OFFSET ?
            """,
                (*params, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def search_albums(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search albums by query (every token must match title/artist)"""
        where, params = self._token_where(
            query, ("fold(a.title)", "fold(ar.name)")
        )
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                f"""
                SELECT COUNT(*) as count FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE {where}
            """,
                params,
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                f"""
                SELECT a.*, ar.name as artist_name
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE {where}
                ORDER BY a.title
                LIMIT ? OFFSET ?
            """,
                (*params, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def search_artists(
        self, query: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Search artists by query"""
        where, params = self._token_where(query, ("fold(name)",))
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                f"SELECT COUNT(*) as count FROM artists WHERE {where}",
                params,
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                f"""
                SELECT * FROM artists
                WHERE {where}
                ORDER BY name
                LIMIT ? OFFSET ?
            """,
                (*params, limit, offset),
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
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
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
        """Get tracks for an album.

        The "Unknown Album" sentinel only owns tracks whose artist is also
        unknown: a loose track WITH a known artist already surfaces under
        that artist (``get_artist_orphan_tracks``), so listing it here too
        would show the same track in two places in the library."""
        artist_cond = (
            " AND t.artist_id = 'unknown_artist'"
            if album_id == "unknown_album" else ""
        )
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                "SELECT COUNT(*) as count FROM tracks t WHERE t.album_id = ?"
                + artist_cond,
                (album_id,),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.album_id = ?
                """
                + artist_cond
                + """
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

    def get_artist_orphan_tracks(
        self, artist_id: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get tracks attributed to an artist whose album is anchored
        elsewhere, so they wouldn't show up under ``get_artist_albums``.

        Two real cases this catches:

          * ``album_id == 'unknown_album'`` (anchored to
            ``unknown_artist``) — the enricher couldn't pick a release.
            Original use case.

          * ``album.artist_id == 'various_artists'`` — a V/A compilation
            the indexer coalesced. The track's ``artist_id`` correctly
            points at the real artist, but the album is anchored to
            the V/A sentinel, so it never surfaces in the
            albums-by-artist query. Without this, an artist with only
            tracks-on-compilations (e.g. one of the 59 distinct artists
            on a Jamendo playlist folder) appears empty in the UI.

        Generalised to ``album.artist_id != track.artist_id`` so any
        future "track anchored under a different album-owner" case
        gets picked up too.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                WHERE t.artist_id = ? AND a.artist_id != t.artist_id
                """,
                (artist_id,),
            )
            total = cursor.fetchone()["count"]

            cursor.execute(
                """
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.artist_id = ? AND a.artist_id != t.artist_id
                ORDER BY t.title COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                (artist_id, limit, offset),
            )

            return [dict(row) for row in cursor.fetchall()], total
        finally:
            conn.close()

    def get_artist_recent_tracks(
        self, artist_id: str, offset: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """Get top tracks for an artist"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM tracks WHERE artist_id = ?
                """,
                (artist_id,),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name
                FROM tracks t
                JOIN albums a ON t.album_id = a.id
                JOIN artists ar ON t.artist_id = ar.id
                WHERE t.artist_id = ?
                ORDER BY t.last_updated DESC
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
        self, offset: int = 0, limit: int = 50, filter_text: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """Get all playlists, optionally filtered by text in name or description."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            if filter_text:
                filter_value = f"%{filter_text.lower()}%"
                # Get total count with filter
                cursor.execute(
                    """
                    SELECT COUNT(*) as count FROM playlists
                    WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ?
                    """,
                    (filter_value, filter_value),
                )
                total = cursor.fetchone()["count"]

                # Get results with filter
                cursor.execute(
                    """
                    SELECT * FROM playlists
                    WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ?
                    ORDER BY last_updated DESC
                    LIMIT ? OFFSET ?
                    """,
                    (filter_value, filter_value, limit, offset),
                )
            else:
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
                SELECT t.*, a.title as album_title, a.genre as album_genre, ar.name as artist_name, 
                       pt.position, pt.playlist_track_id
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
            search_term = f"%{fold_for_match(query)}%"

            # Get total count
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM playlists
                WHERE fold(name) LIKE ? OR fold(description) LIKE ?
                """,
                (search_term, search_term),
            )
            total = cursor.fetchone()["count"]

            # Get results
            cursor.execute(
                """
                SELECT * FROM playlists
                WHERE fold(name) LIKE ? OR fold(description) LIKE ?
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

        from .utils.id_generator import generate_playlist_track_id

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            placeholders = ", ".join("?" for _ in track_ids)
            cursor.execute(
                f"SELECT id FROM tracks WHERE id IN ({placeholders})",
                track_ids,
            )

            # Get set of valid IDs from DB
            valid_id_set = {row["id"] for row in cursor.fetchall()}
            # Preserve order from input
            valid_track_ids = [
                track_id for track_id in track_ids if track_id in valid_id_set
            ]
            if not valid_track_ids:
                return 0

            current_time = int(time.time())

            # Get the next position number
            if allow_duplicates:
                cursor.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                )
            else:
                # Remove any track IDs that are already in the playlist
                placeholders = ", ".join("?" for _ in valid_track_ids)
                cursor.execute(
                    f"""
                    SELECT track_id FROM playlist_tracks 
                    WHERE playlist_id = ? AND track_id IN ({placeholders})
                    """,
                    [playlist_id] + valid_track_ids,
                )
                existing_tracks = {row["track_id"] for row in cursor.fetchall()}
                valid_track_ids = [
                    track_id
                    for track_id in valid_track_ids
                    if track_id not in existing_tracks
                ]

                if not valid_track_ids:
                    return 0

                # Get the next position number
                cursor.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM playlist_tracks WHERE playlist_id = ?",
                    (playlist_id,),
                )

            next_pos = cursor.fetchone()["next_pos"]

            # Insert the tracks with unique playlist_track_ids
            for i, track_id in enumerate(valid_track_ids):
                playlist_track_id = generate_playlist_track_id()
                cursor.execute(
                    """
                    INSERT INTO playlist_tracks (playlist_track_id, playlist_id, track_id, position, added_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        playlist_track_id,
                        playlist_id,
                        track_id,
                        next_pos + i,
                        current_time,
                    ),
                )

            # Update the playlist's track count and duration
            self._update_playlist_stats(cursor, playlist_id)

            conn.commit()
            return len(valid_track_ids)
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
        self, playlist_id: str, playlist_track_ids: List[str]
    ) -> int:
        """
        Remove tracks from a playlist using playlist track IDs.

        Args:
            playlist_id: ID of the playlist
            playlist_track_ids: List of playlist track IDs to remove

        Returns:
            Number of tracks removed
        """
        if not playlist_track_ids:
            return 0

        from .utils.id_generator import generate_playlist_track_id

        conn = self._get_connection()
        try:
            cursor = conn.cursor()

            # Delete the tracks by playlist_track_id
            placeholders = ", ".join("?" for _ in playlist_track_ids)
            cursor.execute(
                f"""
                DELETE FROM playlist_tracks 
                WHERE playlist_id = ? AND playlist_track_id IN ({placeholders})
                """,
                [playlist_id] + playlist_track_ids,
            )

            removed_count = cursor.rowcount

            if removed_count > 0:
                # Reindex the remaining tracks to ensure positions are continuous
                cursor.execute(
                    """
                    SELECT playlist_track_id, track_id FROM playlist_tracks
                    WHERE playlist_id = ?
                    ORDER BY position
                    """,
                    (playlist_id,),
                )

                remaining_tracks = [
                    (row["playlist_track_id"], row["track_id"])
                    for row in cursor.fetchall()
                ]

                # Delete all tracks
                cursor.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
                )

                # Re-insert with new positions
                current_time = int(time.time())
                for position, (playlist_track_id, track_id) in enumerate(
                    remaining_tracks
                ):
                    cursor.execute(
                        """
                        INSERT INTO playlist_tracks (playlist_track_id, playlist_id, track_id, position, added_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            playlist_track_id,
                            playlist_id,
                            track_id,
                            position,
                            current_time,
                        ),
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

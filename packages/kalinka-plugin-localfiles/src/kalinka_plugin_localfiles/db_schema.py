"""
Centralized database schema initialization.

Creates the complete schema in a single transaction before any subprocess
starts, eliminating race conditions from concurrent ALTER TABLE / CREATE TABLE
calls across indexer, searcher, embedder, and enricher processes.

Called once from module_setup.py in the main server process.
"""

from __future__ import annotations

import logging
import os
import time

import aiosqlite

logger = logging.getLogger(__name__.split(".")[-1])

_CLAP_DIMS = 512

_VEC_AUDIO_TABLES = [
    ("vec_tracks_clap", "track_id"),
    ("vec_albums_clap", "album_id"),
    ("vec_artists_clap", "artist_id"),
]

_VEC_TEXT_TABLES = [
    ("vec_tracks_clap_text", "track_id"),
    ("vec_albums_clap_text", "album_id"),
    ("vec_artists_clap_text", "artist_id"),
]


async def init_db(db_path: str) -> None:
    """Create the complete database schema. Safe to call on every startup."""
    db_path = os.path.expanduser(db_path)
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        cursor = await conn.cursor()

        # ---------------------------------------------------------------
        # Core tables (indexer)
        # ---------------------------------------------------------------

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
                last_updated INTEGER,
                embedding_clap BLOB,
                embedding_clap_text BLOB
            )
            """
        )

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
                embedding_clap BLOB,
                embedding_clap_text BLOB,
                FOREIGN KEY (artist_id) REFERENCES artists (id)
            )
            """
        )

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
                search_indexed_at TIMESTAMP,
                tags_predicted TEXT,
                embedding_clap_audio BLOB,
                embedding_version INTEGER DEFAULT 0,
                embedded_at TIMESTAMP,
                embedding_clap_text BLOB,
                FOREIGN KEY (album_id) REFERENCES albums (id),
                FOREIGN KEY (artist_id) REFERENCES artists (id)
            )
            """
        )

        # Negative cache for files whose metadata could not be extracted.
        # Keyed by path + (size, mtime) so a still-uploading / partially
        # written file — which changes size or mtime between scans — keeps
        # missing this cache and is retried, while a stably broken file is
        # parked and skipped instead of being re-read on every scan.
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS indexer_failures (
                file_path     TEXT PRIMARY KEY,
                file_size     BIGINT,
                modified_time INTEGER,
                error         TEXT,
                attempts      INTEGER NOT NULL DEFAULT 1,
                last_attempt  INTEGER
            )
            """
        )

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

        # ---------------------------------------------------------------
        # Indexes
        # ---------------------------------------------------------------

        await cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist_id
            ON playlist_tracks (playlist_id, position)
            """
        )

        # ---------------------------------------------------------------
        # Views
        # ---------------------------------------------------------------

        await cursor.execute(
            """
            CREATE VIEW IF NOT EXISTS recently_added AS
            SELECT * FROM tracks
            ORDER BY last_updated DESC
            """
        )

        # ---------------------------------------------------------------
        # Embedding job queue (shared by searcher + embedder)
        # ---------------------------------------------------------------

        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type   TEXT NOT NULL,
                entity_id     TEXT NOT NULL,
                stage         TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                model_version INTEGER NOT NULL DEFAULT 1,
                attempts      INTEGER NOT NULL DEFAULT 0,
                error         TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, entity_id, stage, model_version)
            )
            """
        )

        await cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_pending
                ON embedding_jobs(status, stage)
                WHERE status IN ('pending', 'failed')
            """
        )

        # ---------------------------------------------------------------
        # Model version registry
        # ---------------------------------------------------------------

        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_model_versions (
                model_name TEXT PRIMARY KEY,
                version    INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for model_name in ("tags", "clap_audio", "clap_text"):
            await cursor.execute(
                "INSERT OR IGNORE INTO embedding_model_versions VALUES (?, 1, CURRENT_TIMESTAMP)",
                (model_name,),
            )

        # ---------------------------------------------------------------
        # FTS5 full-text search
        # ---------------------------------------------------------------

        # Drop legacy contentless fts_tracks table if present
        row = await cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='fts_tracks'"
        )
        existing_sql = await row.fetchone()
        if existing_sql and "content=''" in (existing_sql[0] or ""):
            logger.warning(
                "Detected contentless fts_tracks table — dropping and rebuilding"
            )
            await cursor.execute("DROP TABLE IF EXISTS fts_tracks")
            await cursor.execute("UPDATE tracks SET search_indexed_at = NULL")

        await cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_tracks USING fts5(
                track_id UNINDEXED,
                title,
                artist_name,
                album_title,
                genre_tags,
                tokenize='porter unicode61'
            )
            """
        )

        # ---------------------------------------------------------------
        # Default data
        # ---------------------------------------------------------------

        current_time = int(time.time())
        await cursor.execute(
            "INSERT OR IGNORE INTO artists (id, name, last_updated) VALUES ('unknown_artist', 'Unknown Artist', ?)",
            (current_time,),
        )
        await cursor.execute(
            "INSERT OR IGNORE INTO albums (id, title, artist_id, last_updated) VALUES ('unknown_album', 'Unknown Album', 'unknown_artist', ?)",
            (current_time,),
        )

        await conn.commit()

    # ---------------------------------------------------------------
    # sqlite-vec virtual tables (optional — loaded as extension)
    # ---------------------------------------------------------------

    await _init_vec_tables(db_path)

    logger.info("Database schema initialized: %s", db_path)


async def _init_vec_tables(db_path: str) -> None:
    """Try to load sqlite-vec and create CLAP vector tables."""
    try:
        import sqlite_vec

        async with aiosqlite.connect(db_path) as conn:
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)

            cursor = await conn.cursor()
            for vec_table, pk_col in _VEC_AUDIO_TABLES + _VEC_TEXT_TABLES:
                await cursor.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table}
                    USING vec0({pk_col} TEXT PRIMARY KEY, embedding float[{_CLAP_DIMS}])
                    """
                )
            await conn.commit()

        logger.info("sqlite-vec extension loaded; CLAP vector tables ready")
    except Exception as e:
        logger.warning("sqlite-vec not available (%s); KNN search disabled", e)

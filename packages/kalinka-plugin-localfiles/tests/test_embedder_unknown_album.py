"""Embedder coverage for tracks anchored to the unknown_album sentinel.

V/A compilations and orphan singles keep a known artist but get parented to the
``unknown_album`` placeholder. They used to be skipped by the CLAP embedder
entirely; now they are embedded (audio + ``"Artist - Title"`` text), while
``unknown_artist`` tracks remain excluded and the ``"Unknown Album"`` placeholder
string never leaks into the text vector.
"""

import os
import tempfile

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.embedder.embedder_db import AsyncEmbedderDb


async def _seed(db_path: str) -> None:
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("INSERT INTO artists (id, name) VALUES ('ar1', 'Real Artist')")
        await conn.execute(
            "INSERT INTO albums (id, title, artist_id) VALUES ('al1', 'Real Album', 'ar1')"
        )
        # enriched, fully known -> embedded
        await conn.execute(
            "INSERT INTO tracks (id, title, file_path, format, enriched, artist_id, album_id) "
            "VALUES ('t1', 'Song One', 'f1', 'mp3', 1, 'ar1', 'al1')"
        )
        # enriched, known artist + unknown album (V/A / orphan) -> now embedded
        await conn.execute(
            "INSERT INTO tracks (id, title, file_path, format, enriched, artist_id, album_id) "
            "VALUES ('t2', 'Song Two', 'f2', 'mp3', 2, 'ar1', 'unknown_album')"
        )
        # enriched but unknown artist -> still skipped
        await conn.execute(
            "INSERT INTO tracks (id, title, file_path, format, enriched, artist_id, album_id) "
            "VALUES ('t3', 'Song Three', 'f3', 'mp3', 1, 'unknown_artist', 'al1')"
        )
        # not enriched -> skipped
        await conn.execute(
            "INSERT INTO tracks (id, title, file_path, format, enriched, artist_id, album_id) "
            "VALUES ('t4', 'Song Four', 'f4', 'mp3', 0, 'ar1', 'al1')"
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_schedule_includes_unknown_album_excludes_unknown_artist():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    await _seed(db_path)

    db = AsyncEmbedderDb(LocalFilesConfig(db_path=db_path))
    inserted = await db.schedule_new_jobs(clap_version=1)

    # 2 tracks (t1, t2) x 2 stages (clap_audio, clap_text)
    assert inserted == 4

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT entity_id, stage FROM embedding_jobs")
        rows = await cursor.fetchall()

    audio = {eid for eid, stage in rows if stage == "clap_audio"}
    text = {eid for eid, stage in rows if stage == "clap_text"}
    assert audio == {"t1", "t2"}
    assert text == {"t1", "t2"}


@pytest.mark.asyncio
async def test_metadata_strips_unknown_album_sentinel():
    """An unknown-album track yields "Artist - Title" with no placeholder text."""
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    await _seed(db_path)

    db = AsyncEmbedderDb(LocalFilesConfig(db_path=db_path))

    known = await db.get_track_metadata_for_embedding("t1")
    assert known == {
        "title": "Song One",
        "artist_name": "Real Artist",
        "album_title": "Real Album",
    }

    # unknown_album resolves to the "Unknown Album" row via JOIN; must be blanked.
    unknown_album = await db.get_track_metadata_for_embedding("t2")
    assert unknown_album["title"] == "Song Two"
    assert unknown_album["artist_name"] == "Real Artist"
    assert unknown_album["album_title"] == ""

    # Defensive: the artist sentinel is blanked too (unknown_artist tracks are
    # not scheduled, but the metadata helper must never emit "Unknown Artist").
    unknown_artist = await db.get_track_metadata_for_embedding("t3")
    assert unknown_artist["artist_name"] == ""
    assert unknown_artist["album_title"] == "Real Album"

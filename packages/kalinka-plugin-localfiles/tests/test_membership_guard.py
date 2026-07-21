#!/usr/bin/env python3
"""
Tests for Phase-1 1f: album membership is owned by a single guarded writer,
and AcoustID can no longer create albums or move a track between them.
"""

import aiosqlite
import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb


@pytest.fixture
def config(tmp_path):
    return LocalFilesConfig(
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )


@pytest.mark.asyncio
async def test_reassign_album_touches_only_album_id(config):
    await init_db(config.db_path)
    db = AsyncIndexerDb(config)
    async with aiosqlite.connect(config.db_path) as conn:
        await conn.execute(
            "INSERT INTO tracks (id, title, album_id, artist_id, file_path, "
            "format, mbid, enriched) VALUES ('t1', 'T', 'old_album', 'a', "
            "'/m/t1.flac', 'flac', 'rec-mbid', 1)"
        )
        await conn.execute(
            "INSERT INTO albums (id, title) VALUES ('old_album', 'Old'), "
            "('new_album', 'New')"
        )
        await conn.commit()

    await db.reassign_album("t1", "new_album")

    async with aiosqlite.connect(config.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        row = dict(await (await conn.execute(
            "SELECT * FROM tracks WHERE id='t1'")).fetchone())
    assert row["album_id"] == "new_album"
    assert row["mbid"] == "rec-mbid"   # enricher column preserved
    assert row["enriched"] == 1


def test_acoustid_has_no_album_creation_path():
    # The album-create/track-move code is gone; enrich_track can no longer
    # emit album_id.
    assert not hasattr(AcoustIdPlugin, "_create_or_get_album")

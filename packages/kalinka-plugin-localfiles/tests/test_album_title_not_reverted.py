#!/usr/bin/env python3
"""A corrected album title survives the next scan.

Clustering rebuilds every album title from the folder on every scan, on the
old assumption that titles were always locally derived. That stopped being
true the moment MusicBrainz was allowed to correct a folder-derived one, and
the failure it would cause is invisible on the scan that makes the
correction — the revert lands on the *next* one.
"""

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.indexer.indexer import FileIndexer
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb

# A folder with a typo in it, which is exactly what an external source is for.
TRACK = "Nick Cave/Murder Balads/Stagger Lee.flac"


@pytest_asyncio.fixture
async def indexed(tmp_path):
    music_dir = tmp_path / "music"
    path = music_dir / TRACK
    path.parent.mkdir(parents=True)
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    fi = FileIndexer(config, AsyncIndexerDb(config))
    await fi.process_file(str(path))
    await fi.recluster()
    return fi, config


async def _album(config):
    async with aiosqlite.connect(config.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM albums WHERE id != 'unknown_album'"
        )
        return dict(await cur.fetchone())


@pytest.mark.asyncio
async def test_the_folder_names_it_to_begin_with(indexed):
    _, config = indexed
    assert (await _album(config))["title"] == "Murder Balads"


@pytest.mark.asyncio
async def test_an_externally_resolved_title_is_not_rebuilt(indexed):
    fi, config = indexed
    album_id = (await _album(config))["id"]

    await fi.db_manager.update_album(album_id, {"title": "Murder Ballads"})
    await fi.db_manager.record_resolved_origin(
        "album", album_id, "title", "musicbrainz:rel-1", "inferred"
    )

    await fi.recluster()
    assert (await _album(config))["title"] == "Murder Ballads"


@pytest.mark.asyncio
async def test_a_locally_derived_title_is_still_rebuilt(indexed):
    """The guard must not freeze titles outright: a folder rename, or a
    better reading of the same folder, still has to land."""
    fi, config = indexed
    album_id = (await _album(config))["id"]

    await fi.db_manager.update_album(album_id, {"title": "junk"})
    await fi.recluster()
    assert (await _album(config))["title"] == "Murder Balads"

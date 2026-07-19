#!/usr/bin/env python3
"""
Tests for Phase-0 0c: re-indexing a changed file uses a surgical UPDATE
instead of INSERT OR REPLACE, so enricher/embedder-owned columns survive,
and library_file.first_indexed is written once and never rewritten.
"""

import os

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf
from mutagen.flac import FLAC

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.indexer.indexer import FileIndexer
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb


@pytest_asyncio.fixture
async def indexer(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    return FileIndexer(config, AsyncIndexerDb(config)), music_dir, config


def _write_flac(path, tags):
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = v
    audio.save()


async def _row(config, sql, params):
    async with aiosqlite.connect(config.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None


@pytest.mark.asyncio
async def test_reindex_preserves_enricher_columns(indexer):
    fi, music_dir, config = indexer
    path = music_dir / "01 - track.flac"
    _write_flac(path, {"title": "T", "artist": "A", "album": "Old Album"})
    changes = await fi.process_file(str(path))
    track_id = changes["tracks"]
    old_album_id = (await _row(config, "SELECT album_id FROM tracks WHERE id=?", (track_id,)))[
        "album_id"
    ]

    # Simulate downstream enrichment + embedding on the row.
    async with aiosqlite.connect(config.db_path) as conn:
        await conn.execute(
            "UPDATE tracks SET mbid=?, match_score=?, enriched=1, "
            "embedding_clap_audio=?, embedding_version=3, mood_valence=5.0 "
            "WHERE id=?",
            ("rec-mbid-123", 118, b"\x01\x02\x03", track_id),
        )
        await conn.commit()

    # Retag (new album) and bump mtime so the file is re-processed.
    audio = FLAC(str(path))
    audio["album"] = "New Album"
    audio.save()
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 10))

    await fi.process_file(str(path))

    t = await _row(config, "SELECT * FROM tracks WHERE id=?", (track_id,))
    # Enricher/embedder columns preserved (would be NULL under INSERT OR REPLACE)
    assert t["mbid"] == "rec-mbid-123"
    assert t["match_score"] == 118
    assert t["embedding_clap_audio"] == b"\x01\x02\x03"
    assert t["embedding_version"] == 3
    assert t["mood_valence"] == 5.0
    # Indexer-owned columns updated; enriched reset so it is re-enriched
    assert t["album_id"] != old_album_id
    assert t["enriched"] == 0


@pytest.mark.asyncio
async def test_first_indexed_preserved_across_reindex(indexer):
    fi, music_dir, config = indexer
    path = music_dir / "02 - track.flac"
    _write_flac(path, {"title": "T", "artist": "A", "album": "X"})
    changes = await fi.process_file(str(path))
    track_id = changes["tracks"]

    lf1 = await _row(config, "SELECT * FROM library_file WHERE file_id=?", (track_id,))
    assert lf1 is not None
    assert lf1["first_indexed"] is not None
    assert lf1["device_id"] is not None and lf1["inode"] is not None
    original_first_indexed = lf1["first_indexed"]

    # Reprocess a changed file after time passes.
    audio = FLAC(str(path))
    audio["album"] = "Y"
    audio.save()
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 100))
    await fi.process_file(str(path))

    lf2 = await _row(config, "SELECT * FROM library_file WHERE file_id=?", (track_id,))
    assert lf2["first_indexed"] == original_first_indexed  # never rewritten
    assert lf2["modified_at"] == int(os.stat(path).st_mtime)  # refreshed

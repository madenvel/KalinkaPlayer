#!/usr/bin/env python3
"""
The indexer parses a leading "NN." track number from the filename when the
file has no track-number tag, so an album sorts correctly at scan time (not
only after the enricher's filesystem fallback runs). Regression for albums
whose tracks otherwise sorted lexicographically (1, 10, 2, 3, ...).
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
from kalinka_plugin_localfiles.utils.name_utils import parse_leading_track_number


def test_parse_leading_track_number():
    assert parse_leading_track_number("1. Итальянец (L'Italiano)") == 1
    assert parse_leading_track_number("10. Женщина (Donna)") == 10
    assert parse_leading_track_number("03 - Soli") == 3
    assert parse_leading_track_number("07_ Innamorati") == 7
    assert parse_leading_track_number("Untitled") is None
    assert parse_leading_track_number("No Number Here") is None


def test_parse_no_space_dot_prefix():
    # "N.Title" with no space after the dot (common in ripped folders).
    assert parse_leading_track_number("1.Кончится лето") == 1
    assert parse_leading_track_number("12.Track") == 12
    # A year prefix must never be eaten as a track number (>2 digits).
    assert parse_leading_track_number("1985.Some Song") is None
    assert parse_leading_track_number("2001.A Space Odyssey") is None


@pytest_asyncio.fixture
async def indexer(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    config = LocalFilesConfig(
        music_folders=[str(music)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    return FileIndexer(config, AsyncIndexerDb(config)), music, config


@pytest.mark.asyncio
async def test_indexer_orders_by_filename_number_without_tags(indexer):
    fi, music, config = indexer
    folder = music / "Toto Cutugno - 1985"
    # Ten untagged FLACs named "N. Title" (no track-number tag).
    for n in [1, 2, 3, 10]:
        p = folder / f"{n}. Song {n}.flac"
        p.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(p), np.zeros(2205, dtype="float32"), 44100, format="FLAC")
        await fi.process_file(str(p))

    async with aiosqlite.connect(config.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT track_number, file_path FROM tracks "
            "ORDER BY COALESCE(disc_number,1), track_number, title"
        )
        order = [r["track_number"] for r in await cur.fetchall()]
    # Sorted numerically (1,2,3,10), not lexicographically (1,10,2,3).
    assert order == [1, 2, 3, 10]

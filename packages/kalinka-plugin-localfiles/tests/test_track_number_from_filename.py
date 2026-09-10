#!/usr/bin/env python3
"""
The indexer reads a track number off the path when the file has no
track-number tag, so an album sorts correctly at scan time (not only after
the enricher's filesystem fallback runs). Regression for albums whose tracks
otherwise sorted lexicographically (1, 10, 2, 3, ...).

It goes through the same parser the fallback uses, so the two cannot report
different numbers for one file.
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
from kalinka_plugin_localfiles.filename_model import parse_music_path

MUSIC_ROOT = "/home/user/Music"


def track_number_of(stem: str):
    parsed = parse_music_path(f"{MUSIC_ROOT}/{stem}.flac", MUSIC_ROOT)
    return parsed.track_number if parsed else None


def test_leading_track_number():
    assert track_number_of("1. Итальянец (L'Italiano)") == 1
    assert track_number_of("10. Женщина (Donna)") == 10
    assert track_number_of("03 - Soli") == 3
    assert track_number_of("07_ Innamorati") == 7
    assert track_number_of("Untitled") is None
    assert track_number_of("No Number Here") is None


def test_no_space_dot_prefix():
    # "N.Title" with no space after the dot (common in ripped folders).
    assert track_number_of("1.Кончится лето") == 1
    assert track_number_of("12.Track") == 12
    # A year prefix must never be eaten as a track number.
    assert track_number_of("1985.Some Song") is None
    assert track_number_of("2001.A Space Odyssey") is None


def test_year_prefix_not_eaten_in_spaced_forms():
    # A four-digit year is never a track number, while 100+ tracks on big
    # compilations still parse.
    assert track_number_of("1985. Some Song") is None
    assert track_number_of("1985- Some Song") is None
    assert track_number_of("1985_ Some Song") is None
    assert track_number_of("100. Title") == 100


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

"""Accent-insensitive (ASCII-adjacent) matching for the regular typed search.

The ``search(type, query)`` path runs plain SQL ``LIKE`` against the raw
title/name columns, which compares Unicode codepoints literally — so an
unaccented query like ``oxygene`` never matched ``Oxygène``. Both sides are
now diacritic-folded via the connection-registered ``fold`` SQL function.
See issue #61.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.input_module_db import LocalFilesInputModuleDb


def _seed(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO artists (id, name) VALUES (?, ?)",
            [("ar_starink", "Ed Starink"), ("ar_kabat", "Női Kabát")],
        )
        conn.executemany(
            "INSERT INTO albums (id, title, artist_id) VALUES (?, ?, ?)",
            [("al_synth", "Synthesizer Gréatest", "ar_starink")],
        )
        conn.executemany(
            "INSERT INTO tracks (id, title, album_id, artist_id, file_path, format) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [("t_oxy", "Oxygène", "al_synth", "ar_starink", "/f1", "mp3")],
        )
        conn.commit()


@pytest.fixture
def db(tmp_path):
    cfg = LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    asyncio.run(init_db(cfg.db_path))
    _seed(cfg.db_path)
    return LocalFilesInputModuleDb(cfg)


@pytest.mark.parametrize("query", ["Oxygene", "oxygene", "Oxygène", "OXYGENE"])
def test_track_search_is_accent_insensitive(db, query):
    tracks, total = db.search_tracks(query, 0, 50)
    assert total == 1
    assert tracks[0]["title"] == "Oxygène"


def test_artist_search_is_accent_insensitive(db):
    artists, total = db.search_artists("noi kabat", 0, 50)
    assert total == 1
    assert artists[0]["name"] == "Női Kabát"


def test_album_search_is_accent_insensitive(db):
    albums, total = db.search_albums("greatest", 0, 50)
    assert total == 1
    assert albums[0]["title"] == "Synthesizer Gréatest"


def test_accented_query_still_matches(db):
    """An accented query must still find the (also accented) record — folding
    is symmetric, not lossy in one direction."""
    tracks, total = db.search_tracks("Oxygène", 0, 50)
    assert total == 1

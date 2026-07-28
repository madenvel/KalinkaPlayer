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
            [
                ("ar_starink", "Ed Starink"),
                ("ar_kabat", "Női Kabát"),
                ("ar_jarre", "Jean-Michel Jarre"),
                ("ar_bg", "Борис Гребенщиков"),
            ],
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


@pytest.mark.parametrize(
    "query", ["Jean Michel Jarre", "jean michel jarre", "Jean-Michel Jarre"]
)
def test_artist_search_is_punctuation_insensitive(db, query):
    """A dashless query must find a hyphenated name: the match-fold collapses
    punctuation to spaces, so "Jean Michel Jarre" finds "Jean-Michel Jarre"."""
    artists, total = db.search_artists(query, 0, 50)
    assert total == 1
    assert artists[0]["name"] == "Jean-Michel Jarre"


@pytest.mark.parametrize("query", ["борис гребенщиков", "гребенщиков", "ГРЕБЕНЩИКОВ"])
def test_artist_search_is_unicode_case_insensitive(db, query):
    """SQLite's LIKE only case-folds ASCII, so a lowercase Cyrillic query never
    matched a capitalised Cyrillic name until the fold started casefolding.
    Regression: 'гребенщиков' must find 'Борис Гребенщиков'."""
    artists, total = db.search_artists(query, 0, 50)
    assert total == 1
    assert artists[0]["name"] == "Борис Гребенщиков"


class TestTokenizedRecall:
    """Multi-token queries may span fields: "oxygene jarre" names a track and
    its artist, which no single column contains. Every token must match
    somewhere in the row's own + joined names (issue: work+artist queries
    returned zero candidates, so BEST MATCH had nothing to rank)."""

    def test_track_plus_artist_query_finds_the_track(self, db):
        # "Oxygène" is by Ed Starink here; the track title + artist name
        # together cover the query even though neither column does alone.
        tracks, total = db.search_tracks("oxygene starink", 0, 50)
        assert total == 1
        assert tracks[0]["title"] == "Oxygène"

    def test_album_plus_artist_query_finds_the_album(self, db):
        albums, total = db.search_albums("synthesizer starink", 0, 50)
        assert total == 1
        assert albums[0]["title"] == "Synthesizer Gréatest"

    def test_unrelated_token_still_excludes(self, db):
        # A token no field explains must keep the row out — tokenization must
        # not degrade into any-token OR matching.
        tracks, total = db.search_tracks("oxygene jarre", 0, 50)
        assert total == 0

    def test_single_token_behaves_as_before(self, db):
        tracks, total = db.search_tracks("oxygene", 0, 50)
        assert total == 1

"""An artist row carries how many albums the library credits them with, so the
client can print "N albums" without a round-trip per row."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.input_module_db import LocalFilesInputModuleDb
from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule


def _seed(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO artists (id, name, last_updated) VALUES (?, ?, ?)",
            [
                ("ar_air", "Air", 130),
                ("ar_queen", "Queen", 120),
                ("ar_loose", "Loose Ends", 110),
            ],
        )
        conn.executemany(
            "INSERT INTO albums (id, title, artist_id, last_updated)"
            " VALUES (?, ?, ?, ?)",
            [
                ("al_moon", "Moon Safari", "ar_air", 105),
                ("al_hertz", "Everybody Hertz", "ar_air", 104),
                ("al_innuendo", "Innuendo", "ar_queen", 103),
            ],
        )
        # A single with no album of its own: its artist counts zero albums.
        conn.execute(
            "INSERT INTO tracks (id, title, album_id, artist_id, file_path,"
            " format, duration, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("t_solo", "B-side", "unknown_album", "ar_loose", "/1", "flac", 90, 92),
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


class _NoArtwork:
    """Enough db for the browse item: it looks up an image, finds none."""

    def get_artist_by_id(self, artist_id):
        return None


def _by_name(rows):
    return {row["name"]: row["album_count"] for row in rows}


def test_artist_listing_counts_albums(db):
    rows, _ = db.list_kind("artist", 0, 50)
    counts = _by_name(rows)
    assert counts["Air"] == 2
    assert counts["Queen"] == 1
    assert counts["Loose Ends"] == 0


def test_artist_search_counts_albums(db):
    rows, _ = db.search_artists("air", 0, 50)
    assert _by_name(rows) == {"Air": 2}


def test_single_artist_lookup_counts_albums(db):
    assert db.get_artist_by_id("ar_queen")["album_count"] == 1


def test_batch_artist_lookup_counts_albums(db):
    rows = db.get_artists_by_ids(["ar_queen", "ar_air"])
    assert [row["album_count"] for row in rows] == [1, 2]


@pytest.mark.parametrize("count,expected", [(3, 3), (0, None), (None, None)])
def test_browse_item_reports_the_count_and_omits_a_zero(tmp_path, count, expected):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path / "music")],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    module = LocalFilesInputModule(config, _NoArtwork())
    artist = {"id": "ar_air", "name": "Air"}
    if count is not None:
        artist["album_count"] = count

    item = module._create_artist_browse_item(artist)

    assert item.artist.album_count == expected

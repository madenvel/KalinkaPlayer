#!/usr/bin/env python3
"""A side-lettered rip whose tags look complete.

"A1 In the flesh.flac" tags artist, title, year and a track number that
restarts at 1 on every side, and no DISCNUMBER anywhere. Nothing in that is
missing, so the path was never consulted and the four sides interleaved:
every "1" together, then every "2". The side lives in the file's own name,
which is the one disc number a fully tagged rip can still be missing.
"""

import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf
from mutagen.flac import FLAC

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.filename_model import names_a_vinyl_side
from kalinka_plugin_localfiles.indexer.indexer import _tags_leave_a_gap
from kalinka_plugin_localfiles.indexer.indexer import FileIndexer
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb

ALBUM = "Music/The Wall (flac 24-192)"
SIDES = {"A": ["In the flesh", "The thin ice"], "B": ["Goodbye blue sky", "Empty spaces"]}
FULLY_TAGGED = {"artist": "Pink Floyd", "title": "t", "track_number": 1, "year": 1979}


class TestTheMarkerIsRecognised:
    @pytest.mark.parametrize(
        "name",
        ["A1 In the flesh.flac", "B1 Goodbye blue sky.flac", "D7 Outside the wall.flac",
         "C12 Something.flac", "b2_lowercase.flac", "A1-Dashed.flac"],
    )
    def test_a_side_marker_is_seen(self, name):
        assert names_a_vinyl_side(f"/music/The Wall/{name}") is True

    @pytest.mark.parametrize(
        "name",
        ["01 In the flesh.flac", "In the flesh.flac", "Z1 Not a side.flac",
         "A1.flac", "A100 Too many digits.flac", "AB1 Not a side.flac"],
    )
    def test_anything_else_is_not(self, name):
        assert names_a_vinyl_side(f"/music/The Wall/{name}") is False


class TestTheGateOpensForIt:
    def test_complete_tags_still_ask_the_path_about_the_side(self):
        """The regression: nothing was missing, so nothing ever asked."""
        path = "/music/Music/The Wall (flac 24-192)/B1 Goodbye blue sky.flac"
        assert _tags_leave_a_gap(FULLY_TAGGED, path) is True

    def test_complete_tags_without_a_marker_still_skip_the_parse(self):
        """The parse is not free; a fully tagged ordinary file must not pay."""
        path = "/music/Pink Floyd/The Wall/06 Mother.flac"
        assert _tags_leave_a_gap(FULLY_TAGGED, path) is False

    def test_a_tagged_disc_number_settles_it(self):
        """A rip that names its own disc is not asked again."""
        path = "/music/Music/The Wall/B1 Goodbye blue sky.flac"
        assert _tags_leave_a_gap({**FULLY_TAGGED, "disc_number": 2}, path) is False


@pytest_asyncio.fixture
async def indexed(tmp_path):
    music = tmp_path / "music"
    (music / ALBUM).mkdir(parents=True)
    config = LocalFilesConfig(
        music_folders=[str(music)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    indexer = FileIndexer(config, AsyncIndexerDb(config))
    for side, titles in SIDES.items():
        for number, title in enumerate(titles, 1):
            path = music / ALBUM / f"{side}{number} {title}.flac"
            sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
            audio = FLAC(str(path))
            audio["title"] = f"{side}{number} {title}"
            audio["artist"] = "Pink Floyd"
            audio["album"] = "The Wall"
            audio["date"] = "1979"
            audio["tracknumber"] = str(number)
            audio.save()
            await indexer.process_file(str(path))
    await indexer.recluster()
    return config


@pytest.mark.asyncio
async def test_the_sides_stop_interleaving(indexed):
    """End to end, in the order the album is served in."""
    import aiosqlite

    async with aiosqlite.connect(indexed.db_path) as conn:
        cursor = await conn.execute(
            "SELECT disc_number, track_number, title FROM tracks "
            "ORDER BY COALESCE(disc_number, 1), track_number, title"
        )
        rows = await cursor.fetchall()
    assert rows == [
        (1, 1, "A1 In the flesh"),
        (1, 2, "A2 The thin ice"),
        (2, 1, "B1 Goodbye blue sky"),
        (2, 2, "B2 Empty spaces"),
    ]

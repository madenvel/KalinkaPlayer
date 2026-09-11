#!/usr/bin/env python3
"""New weights reach a library that is already indexed.

A scan skips a file whose size and mtime are unchanged, so nothing would
otherwise re-read a path after a retrain. This pass is what replaced the
enrichment fingerprint when path parsing moved out of the enricher, and it is
deliberately narrower than the thing it replaced: only rows whose title
nothing but their own path ever supplied are re-read.
"""

import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf
from mutagen.flac import FLAC

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.db_schema import init_db
from kalinka_plugin_localfiles.indexer.indexer import FileIndexer
from kalinka_plugin_localfiles.indexer.indexer_db import AsyncIndexerDb

GUESSED = "Nick Cave/Murder Ballads/Stagger Lee.flac"
TAGGED = "Nick Cave/Murder Ballads/Song.flac"


def _write(path, tags=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    if tags:
        audio = FLAC(str(path))
        for k, v in tags.items():
            audio[k] = v
        audio.save()
    return path


@pytest_asyncio.fixture
async def indexed(tmp_path):
    music_dir = tmp_path / "music"
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
        quiescence_seconds=0,
    )
    await init_db(config.db_path)
    fi = FileIndexer(config, AsyncIndexerDb(config))
    await fi.process_file(str(_write(music_dir / GUESSED)))
    await fi.process_file(str(_write(
        music_dir / TAGGED,
        {"title": "Song", "artist": "Nick Cave", "album": "Murder Ballads",
         "tracknumber": "1", "date": "1996"},
    )))
    return fi, music_dir, config


def _spy(fi, seen):
    real = fi.process_file

    async def spy(path, force=False):
        seen.append(path)
        return await real(path, force=force)

    fi.process_file = spy


async def _run(fi, music_dir):
    seen = []
    _spy(fi, seen)
    count = await fi._reparse_paths_on_model_change(
        [str(music_dir)], {"artists": set(), "albums": set(), "tracks": set()}
    )
    return count, seen


@pytest.mark.asyncio
async def test_only_the_path_named_row_is_re_read(indexed):
    fi, music_dir, _ = indexed
    count, seen = await _run(fi, music_dir)
    assert count == 1
    assert [p.split("/")[-1] for p in seen] == ["Stagger Lee.flac"]


@pytest.mark.asyncio
async def test_an_unchanged_model_re_reads_nothing(indexed):
    fi, music_dir, _ = indexed
    await _run(fi, music_dir)  # first pass records the current identity
    count, seen = await _run(fi, music_dir)
    assert (count, seen) == (0, [])


@pytest.mark.asyncio
async def test_the_identity_is_recorded_even_with_nothing_to_re_read(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    config = LocalFilesConfig(
        music_folders=[str(music_dir)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    await init_db(config.db_path)
    fi = FileIndexer(config, AsyncIndexerDb(config))
    assert await fi.db_manager.get_filename_model_identity() is None
    await fi._reparse_paths_on_model_change(
        [str(music_dir)], {"artists": set(), "albums": set(), "tracks": set()}
    )
    # Otherwise an empty library would re-run the query on every scan.
    assert await fi.db_manager.get_filename_model_identity() is not None


@pytest.mark.asyncio
async def test_a_file_under_an_unavailable_root_is_skipped(indexed):
    """A dead mount must not be walked, and must not be treated as an empty
    library either — the row keeps its old value and is retried later."""
    fi, _, _ = indexed
    seen = []
    _spy(fi, seen)
    count = await fi._reparse_paths_on_model_change(
        [], {"artists": set(), "albums": set(), "tracks": set()}
    )
    assert (count, seen) == (0, [])
    # Recording it would close the door on those tracks for good.
    assert await fi.db_manager.get_filename_model_identity() is None

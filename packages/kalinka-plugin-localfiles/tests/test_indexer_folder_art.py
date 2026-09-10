#!/usr/bin/env python3
"""Covers taken from the album folder at scan time.

A needledrop or a download often keeps its sleeve as a plain file next to the
audio rather than tagged into it, and for a vinyl rip that scan is the most
authoritative art there is. It costs no network, so it is taken during the
scan rather than waiting behind the enricher — but only where the file itself
carried nothing, since a picture inside the file is unambiguously this
record's while a folder may hold a whole sleeve set.
"""

import io

import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf
from mutagen.flac import FLAC, Picture
from PIL import Image

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
    db = AsyncIndexerDb(config)
    return FileIndexer(config, db), db, music_dir, config


def _cover_bytes(color, size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _write_flac(path, tags, cover=None):
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    audio = FLAC(str(path))
    for k, v in tags.items():
        audio[k] = v
    if cover is not None:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/png"
        pic.data = cover
        audio.add_picture(pic)
    audio.save()


async def _album_with(music_dir, fi, *, folder_images=(), embedded=None):
    """One tagged album folder, optionally shipping sidecar images."""
    folder = music_dir / "Nick Cave - Murder Ballads"
    folder.mkdir()
    _write_flac(
        folder / "01 Stagger Lee.flac",
        {"title": "Stagger Lee", "artist": "Nick Cave", "album": "Murder Ballads"},
        cover=embedded,
    )
    for name, size in folder_images:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (30, 90, 140)).save(target)
    changes = await fi.process_file(str(folder / "01 Stagger Lee.flac"))
    return folder, changes["albums"]


@pytest.mark.asyncio
async def test_a_sleeve_beside_the_audio_becomes_the_cover(indexer):
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(
        music_dir, fi, folder_images=[("cover.jpg", (500, 500))]
    )

    assert (await db.get_album_by_id(album_id))["image_url"] is None
    counts = await fi.backfill_folder_art([str(music_dir)])

    assert counts == {"albums": 1}
    album = await db.get_album_by_id(album_id)
    assert album["image_url"] == f"{album_id}.jpg"
    assert album["image_generated"] == 0
    for suffix in ("thumbnail", "small", "large"):
        assert (fi.artwork_path / "album" / f"{album_id}_{suffix}.jpg").exists()


@pytest.mark.asyncio
async def test_a_scan_subfolder_is_searched(indexer):
    """Sleeve scans are commonly filed under PIC/ rather than beside the audio."""
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(
        music_dir, fi, folder_images=[("PIC/sleeve_1.jpg", (2000, 2000))]
    )

    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 1
    assert (await db.get_album_by_id(album_id))["image_url"] == f"{album_id}.jpg"


@pytest.mark.asyncio
async def test_embedded_art_is_not_replaced(indexer):
    """A picture inside the file already speaks for this record; the folder
    pass exists for what the file left unanswered."""
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(
        music_dir,
        fi,
        folder_images=[("cover.jpg", (500, 500))],
        embedded=_cover_bytes((250, 200, 20)),
    )

    before = await db.get_album_by_id(album_id)
    assert before["image_url"] == f"{album_id}.jpg"

    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 0


@pytest.mark.asyncio
async def test_a_folder_with_no_images_is_left_alone(indexer):
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(music_dir, fi)

    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 0
    assert (await db.get_album_by_id(album_id))["image_url"] is None


@pytest.mark.asyncio
async def test_a_generated_placeholder_is_upgraded(indexer):
    """The sweep that re-opens placeholder albums clears image_url, but a
    cover the generator drew must not keep a real one out either way."""
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(
        music_dir, fi, folder_images=[("cover.jpg", (500, 500))]
    )
    await db.update_album(album_id, {"image_url": album_id, "image_generated": 1})

    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 1
    album = await db.get_album_by_id(album_id)
    assert album["image_generated"] == 0


@pytest.mark.asyncio
async def test_the_pass_is_idempotent(indexer):
    fi, db, music_dir, _ = indexer
    await _album_with(music_dir, fi, folder_images=[("cover.jpg", (500, 500))])

    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 1
    assert (await fi.backfill_folder_art([str(music_dir)]))["albums"] == 0


@pytest.mark.asyncio
async def test_an_unavailable_root_is_not_touched(indexer):
    """A folder on an unmounted drive must not be read, and must not have its
    cover cleared for being unreadable."""
    fi, db, music_dir, _ = indexer
    _, album_id = await _album_with(
        music_dir, fi, folder_images=[("cover.jpg", (500, 500))]
    )

    assert (await fi.backfill_folder_art([]))["albums"] == 0
    assert (await db.get_album_by_id(album_id))["image_url"] is None

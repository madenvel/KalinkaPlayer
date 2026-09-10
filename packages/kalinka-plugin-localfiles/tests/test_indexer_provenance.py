#!/usr/bin/env python3
"""Every value the indexer writes says where it came from.

Provenance is what replaced the old "title == basename" echo guard. It is
also what lets MusicBrainz correct a folder's typo without being allowed to
overrule a tag, and what tells AcoustID whether a track is really identified
or merely named after its own file.
"""

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

TAGS = {"title": "Stagger Lee", "artist": "Nick Cave", "album": "Murder Ballads"}


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


def _write(path, tags=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(4410, dtype="float32"), 44100, format="FLAC")
    if tags:
        audio = FLAC(str(path))
        for k, v in tags.items():
            audio[k] = v
        audio.save()
    return path


async def _origins(config):
    async with aiosqlite.connect(config.db_path) as conn:
        cur = await conn.execute(
            "SELECT entity_type, field, source, tier FROM resolved_origin"
        )
        return {(r[0], r[1]): (r[2], r[3]) for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_a_tagged_file_is_observed(indexer):
    fi, music_dir, config = indexer
    await fi.process_file(str(_write(music_dir / "x.flac", TAGS)))
    origins = await _origins(config)
    assert origins[("track", "title")] == ("tag_consensus", "observed")
    assert origins[("artist", "name")] == ("tag_consensus", "observed")


@pytest.mark.asyncio
async def test_a_path_derived_file_is_a_guess(indexer):
    fi, music_dir, config = indexer
    await fi.process_file(
        str(_write(music_dir / "Nick Cave/Murder Ballads/Stagger Lee.flac"))
    )
    origins = await _origins(config)
    # The two sources the resolver reserves for a path, and the tier every
    # external match outranks.
    assert origins[("track", "title")] == ("filename", "guessed")
    assert origins[("artist", "name")] == ("folder_name", "guessed")


@pytest.mark.asyncio
async def test_the_album_title_is_a_guess_once_clustering_names_it(indexer):
    fi, music_dir, config = indexer
    await fi.process_file(
        str(_write(music_dir / "Nick Cave/Murder Ballads/Stagger Lee.flac"))
    )
    await fi.recluster()
    assert (await _origins(config))[("album", "title")] == (
        "folder_name", "guessed",
    )


class TestASharedRowKeepsItsStrongestOrigin:
    """An artist row is shared by every file that names it, so the order a
    folder happens to be walked in must not decide its provenance."""

    @pytest.mark.asyncio
    async def test_a_guess_does_not_displace_a_tag(self, indexer):
        fi, music_dir, config = indexer
        folder = music_dir / "Nick Cave" / "Murder Ballads"
        await fi.process_file(str(_write(folder / "01 Song.flac", TAGS)))
        await fi.process_file(str(_write(folder / "02 Stagger Lee.flac")))
        assert (await _origins(config))[("artist", "name")] == (
            "tag_consensus", "observed",
        )

    @pytest.mark.asyncio
    async def test_a_tag_does_displace_a_guess(self, indexer):
        fi, music_dir, config = indexer
        folder = music_dir / "Nick Cave" / "Murder Ballads"
        await fi.process_file(str(_write(folder / "02 Stagger Lee.flac")))
        assert (await _origins(config))[("artist", "name")] == (
            "folder_name", "guessed",
        )
        await fi.process_file(str(_write(folder / "01 Song.flac", TAGS)))
        assert (await _origins(config))[("artist", "name")] == (
            "tag_consensus", "observed",
        )


@pytest.mark.asyncio
async def test_the_placeholder_rows_are_left_out(indexer):
    """unknown_artist/unknown_album are shared by the whole library and name
    nothing, so they have no provenance to record."""
    fi, music_dir, config = indexer
    await fi.process_file(str(_write(music_dir / "Stagger Lee.flac")))
    async with aiosqlite.connect(config.db_path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM resolved_origin WHERE entity_id LIKE 'unknown_%'"
        )
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_provenance_dies_with_the_row_it_describes(indexer):
    """Neither provenance table has a foreign key, and entity ids are
    content-derived so nothing ever reclaims one — a deleted album's origin
    would outlive it forever. Clustering replaces album rows on most scans,
    so this is a steady drip, not a rare case."""
    fi, _, config = indexer
    await fi.db_manager.insert_album(
        {"id": "album_ghost", "title": "Gone", "artist_id": "unknown_artist"}
    )
    await fi.db_manager.record_resolved_origin(
        "album", "album_ghost", "title", "folder_name", "guessed"
    )
    await fi.db_manager.record_claim(
        "album", "album_ghost", "year", 1995, "musicbrainz:r1", "inferred"
    )

    deleted_albums, _ = await fi.db_manager.delete_orphaned_albums_and_artists()
    assert deleted_albums == 1

    async with aiosqlite.connect(config.db_path) as conn:
        for table in ("resolved_origin", "metadata_claims"):
            cur = await conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE entity_id = 'album_ghost'"
            )
            assert (await cur.fetchone())[0] == 0, table


@pytest.mark.asyncio
async def test_a_live_row_keeps_its_provenance(indexer):
    """The sweep must not take the provenance of everything else with it."""
    fi, music_dir, config = indexer
    await fi.process_file(str(_write(music_dir / "Nick Cave/Album/Song.flac")))
    await fi.db_manager.delete_orphaned_albums_and_artists()
    assert ("artist", "name") in await _origins(config)

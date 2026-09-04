#!/usr/bin/env python3
"""Rows the enricher has not reached yet can have a NULL artist name; browse
and search output must still build (the suggestions engine probes ai_search
mid-enrichment and a pydantic failure there silently drops the probe).
"""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule


class _FakeDb:
    def __init__(self, track):
        self._track = track

    def is_good(self):
        return True

    def get_tracks_by_ids(self, track_ids):
        return [self._track] if self._track["id"] in track_ids else []

    def get_album_by_id(self, album_id):
        return None


def _module(tmp_path, track):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path / "music")],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return LocalFilesInputModule(config, _FakeDb(track))


@pytest.mark.asyncio
async def test_track_with_nameless_artist_still_builds(tmp_path):
    track = {
        "id": "track_1",
        "album_id": "album_1",
        "artist_id": "artist_1",
        "album_title": "Album",
        "artist_name": None,  # not yet enriched
        "title": "Song",
        "duration": 100,
        "format": "audio/mpeg",
        "file_path": str(tmp_path / "music" / "song.mp3"),
    }

    module = _module(tmp_path, track)
    [info] = await module.get_track_info(["track_1"])

    assert info.metadata.performer.name == "Unknown artist"
    assert info.metadata.album.artist.name == "Unknown artist"


@pytest.mark.asyncio
async def test_track_browse_item_with_nameless_artist_still_builds(tmp_path):
    track = {
        "id": "track_1",
        "album_id": "album_1",
        "artist_id": "artist_1",
        "album_title": "Album",
        "artist_name": None,
        "title": "Song",
        "duration": 100,
        "format": "audio/mpeg",
        "file_path": str(tmp_path / "music" / "song.mp3"),
    }

    module = _module(tmp_path, track)
    item = module._create_track_browse_item(track)

    assert item.subname is None
    assert item.track.performer.name == "Unknown artist"

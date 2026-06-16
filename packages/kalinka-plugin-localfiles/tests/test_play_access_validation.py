#!/usr/bin/env python3
"""Issue #60 (play side): a track's link_retriever must validate access.

The play queue calls ``link_retriever()`` to get a stream URL and treats any
exception as "track unavailable". A local-files track whose file has moved out
of the configured music folders, or is no longer readable, must therefore raise
rather than return a ``file://`` URL the player can't use.
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
        # No artwork on disk in these tests; None keeps the cover lookup quiet.
        return None


def _module(tmp_path, music_folders, track):
    config = LocalFilesConfig(
        music_folders=[str(f) for f in music_folders],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    return LocalFilesInputModule(config, _FakeDb(track))


def _track(file_path):
    return {
        "id": "track_1",
        "album_id": "album_1",
        "artist_id": "artist_1",
        "album_title": "Album",
        "artist_name": "Artist",
        "title": "Song",
        "duration": 100,
        "format": "audio/mpeg",
        "file_path": str(file_path),
    }


@pytest.mark.asyncio
async def test_link_retriever_returns_url_for_in_config_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"
    path.write_bytes(b"x")

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    url = await info.link_retriever()
    assert url.url == f"file://{path}"
    assert url.format == "audio/mpeg"


@pytest.mark.asyncio
async def test_link_retriever_raises_for_out_of_config_file(tmp_path):
    music = tmp_path / "music"
    other = tmp_path / "other"
    music.mkdir()
    other.mkdir()
    path = other / "song.mp3"
    path.write_bytes(b"x")  # the file exists, but is out of scope

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    with pytest.raises(PermissionError):
        await info.link_retriever()


@pytest.mark.asyncio
async def test_link_retriever_raises_for_missing_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"  # in config, but never created

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    with pytest.raises(FileNotFoundError):
        await info.link_retriever()

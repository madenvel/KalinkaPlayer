#!/usr/bin/env python3
"""Issue #60 (play side): access is validated whenever a track is played.

The play queue calls ``source_retriever()`` to get a track's source and treats
any exception as "track unavailable"; the server calls ``get_content_info()``
again before serving the bytes. A track whose file has moved out of the
configured music folders, or is no longer readable, must fail both — the
retriever by raising, the content lookup by reporting the asset absent.
"""

import pytest

from kalinka_plugin_sdk.inputmodule import ModuleAsset

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule


class _FakeDb:
    def __init__(self, track):
        self._track = track

    def is_good(self):
        return True

    def get_tracks_by_ids(self, track_ids):
        return [self._track] if self._track["id"] in track_ids else []

    def get_track_by_id(self, track_id):
        return self._track if self._track["id"] == track_id else None

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
async def test_source_retriever_names_an_asset_for_in_config_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"
    path.write_bytes(b"x")

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    source = await info.source_retriever()
    assert source.source == ModuleAsset(module="localfiles", asset_id="track_1")
    assert source.format == "audio/mpeg"


@pytest.mark.asyncio
async def test_source_retriever_raises_for_out_of_config_file(tmp_path):
    music = tmp_path / "music"
    other = tmp_path / "other"
    music.mkdir()
    other.mkdir()
    path = other / "song.mp3"
    path.write_bytes(b"x")  # the file exists, but is out of scope

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    with pytest.raises(PermissionError):
        await info.source_retriever()


@pytest.mark.asyncio
async def test_source_retriever_raises_for_missing_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"  # in config, but never created

    module = _module(tmp_path, [music], _track(path))
    [info] = await module.get_track_info(["track_1"])
    with pytest.raises(FileNotFoundError):
        await info.source_retriever()


@pytest.mark.asyncio
async def test_content_info_resolves_in_config_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"
    path.write_bytes(b"x")

    module = _module(tmp_path, [music], _track(path))
    info = await module.get_content_info("track_1")
    assert info is not None
    assert info.local_path == str(path)
    assert info.mime_type == "audio/mpeg"
    assert info.cacheable is True


@pytest.mark.asyncio
async def test_content_info_refuses_out_of_config_file(tmp_path):
    music = tmp_path / "music"
    other = tmp_path / "other"
    music.mkdir()
    other.mkdir()
    path = other / "song.mp3"
    path.write_bytes(b"x")

    module = _module(tmp_path, [music], _track(path))
    assert await module.get_content_info("track_1") is None


@pytest.mark.asyncio
async def test_content_info_refuses_missing_file(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"  # in config, but never created

    module = _module(tmp_path, [music], _track(path))
    assert await module.get_content_info("track_1") is None


@pytest.mark.asyncio
async def test_content_info_refuses_unknown_asset(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    path = music / "song.mp3"
    path.write_bytes(b"x")

    module = _module(tmp_path, [music], _track(path))
    assert await module.get_content_info("no_such_track") is None

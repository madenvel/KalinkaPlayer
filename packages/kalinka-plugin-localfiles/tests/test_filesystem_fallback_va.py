"""Tests for V/A-folder awareness in ``FilesystemFallbackPlugin``.

The indexer's ``orphan_va_folder_tracks`` detaches tracks in V/A
folders to ``unknown_album`` so they surface as singles under their
real artist. Without the corresponding check in the filesystem
fallback, enrichment would re-anchor those same tracks to
(folder, tag-title) albums anchored to whichever artist happened to
be tagged first — undoing the detach on every enrichment pass.

These tests pin the V/A-aware behaviour: filesystem_fallback skips
album reassignment when the track's folder has ≥4 distinct real
artists and ≥0.5 unique-artist-per-track ratio. Outside V/A folders
the old fallback behaviour is preserved.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.filesystem_fallback_plugin import (
    FilesystemFallbackPlugin,
)


class FakeDb:
    """Stand-in db_manager that the plugin can call against."""

    def __init__(
        self,
        artists_in_folder: int,
        tracks_in_folder: int,
        existing_artist: Optional[Dict] = None,
        existing_album: Optional[Dict] = None,
    ) -> None:
        self._artists_in_folder = artists_in_folder
        self._tracks_in_folder = tracks_in_folder
        self.existing_artist = existing_artist
        self.existing_album = existing_album
        # Capture mutations so tests can assert what happened.
        self.inserted_artists: list = []
        self.inserted_albums: list = []
        self.search_calls: list = []

    async def count_distinct_artists_in_folder(self, folder: str) -> int:
        return self._artists_in_folder

    async def count_tracks_in_folder(self, folder: str) -> int:
        return self._tracks_in_folder

    async def search_artists(self, name: str, limit: int) -> Tuple[list, int]:
        self.search_calls.append(("artist", name))
        return [], 0

    async def get_artist_by_id(self, artist_id: str) -> Optional[Dict]:
        return self.existing_artist

    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        return self.existing_album

    async def insert_artist(self, data: Dict[str, Any]) -> None:
        self.inserted_artists.append(data)

    async def insert_album(self, data: Dict[str, Any]) -> None:
        self.inserted_albums.append(data)


def _plugin(db: FakeDb) -> FilesystemFallbackPlugin:
    cfg = LocalFilesConfig(
        music_folders=["/mnt/music"],
        db_path="/tmp/x.db",
        artwork_path="/tmp/artwork",
    )
    return FilesystemFallbackPlugin(cfg, db)


@pytest.mark.asyncio
async def test_va_folder_track_stays_in_unknown_album():
    """Wordsmith's situation: track tag fully present, but the folder
    has 59 distinct artists across 69 tracks. ``filesystem_fallback``
    must NOT re-anchor the track — leave it in ``unknown_album`` so
    the artist page surfaces it via the orphan-tracks fallback."""
    db = FakeDb(artists_in_folder=59, tracks_in_folder=69)
    plugin = _plugin(db)

    track = {
        "id": "t1",
        "title": "On the Come Up",
        "artist_id": "artist_wordsmith",
        "album_id": "unknown_album",
        "file_path": "/mnt/music/Playlist - Urban/049-Wordsmith-On the Come Up.mp3",
    }
    result = await plugin.enrich_track(track)
    # Either None (no changes) or an updates dict that does NOT touch album_id.
    if result is not None:
        assert "album_id" not in result.get("updates", {})
    # No new album was created (because the track stays in unknown_album).
    assert db.inserted_albums == []


@pytest.mark.asyncio
async def test_single_artist_folder_track_does_get_anchored():
    """Outside V/A folders, the fallback's existing behaviour should
    be preserved: a track in ``unknown_album`` whose folder has one
    artist gets anchored to a derived album."""
    db = FakeDb(artists_in_folder=1, tracks_in_folder=10)
    plugin = _plugin(db)

    track = {
        "id": "t2",
        "title": "Money",
        "artist_id": "artist_pinkfloyd",
        "album_id": "unknown_album",
        "file_path": (
            "/mnt/music/Pink Floyd - Dark Side of the Moon/06 Money.flac"
        ),
    }
    result = await plugin.enrich_track(track)
    assert result is not None
    assert "album_id" in result["updates"]
    assert result["updates"]["album_id"] != "unknown_album"


@pytest.mark.asyncio
async def test_already_anchored_track_is_left_alone_in_va_folder():
    """If a track already has a non-unknown album_id, the fallback
    doesn't touch ``album_id`` regardless of folder status — that's
    pre-existing behaviour. Confirming it still holds in V/A folders."""
    db = FakeDb(artists_in_folder=59, tracks_in_folder=69)
    plugin = _plugin(db)
    track = {
        "id": "t3",
        "title": "Some Title",
        "artist_id": "artist_x",
        "album_id": "album_xyz",  # already anchored
        "file_path": "/mnt/music/Playlist - Urban/02-x.mp3",
    }
    result = await plugin.enrich_track(track)
    if result is not None:
        assert "album_id" not in result.get("updates", {})


@pytest.mark.asyncio
async def test_va_check_is_cached_per_folder():
    """A 69-track V/A folder should produce 2 DB queries (count
    artists + count tracks) total across all of its tracks, not 138.
    Counts the queries via the FakeDb call log."""
    db = FakeDb(artists_in_folder=59, tracks_in_folder=69)
    plugin = _plugin(db)

    # Monkey-patch the counters to log every call.
    artist_calls = {"n": 0}
    track_calls = {"n": 0}
    orig_artists = db.count_distinct_artists_in_folder
    orig_tracks = db.count_tracks_in_folder

    async def counting_artists(folder):
        artist_calls["n"] += 1
        return await orig_artists(folder)

    async def counting_tracks(folder):
        track_calls["n"] += 1
        return await orig_tracks(folder)

    db.count_distinct_artists_in_folder = counting_artists  # type: ignore
    db.count_tracks_in_folder = counting_tracks  # type: ignore

    for i in range(10):
        await plugin.enrich_track(
            {
                "id": f"t{i}",
                "title": f"T{i}",
                "artist_id": f"artist_{i}",
                "album_id": "unknown_album",
                "file_path": f"/mnt/music/Playlist - Urban/{i:02d}.mp3",
            }
        )

    # Cache hit for every call after the first folder lookup.
    assert artist_calls["n"] == 1
    assert track_calls["n"] == 1


@pytest.mark.asyncio
async def test_va_threshold_just_below_does_not_skip():
    """At 3 distinct artists, just below the floor of 4, the fallback
    should NOT consider the folder V/A — it anchors the track as
    before."""
    db = FakeDb(artists_in_folder=3, tracks_in_folder=10)
    plugin = _plugin(db)
    track = {
        "id": "t4",
        "title": "Track",
        "artist_id": "artist_x",
        "album_id": "unknown_album",
        "file_path": "/mnt/music/Some Folder/track.mp3",
    }
    result = await plugin.enrich_track(track)
    assert result is not None
    # The fallback either anchored it or punted; what it should NOT do
    # is silently leave it in unknown_album for "V/A" reasons.
    assert (
        "album_id" in result["updates"]
        or "title" in result["updates"]
        or "artist_id" in result["updates"]
    )

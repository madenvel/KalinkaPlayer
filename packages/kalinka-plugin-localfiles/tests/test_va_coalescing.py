"""Tests for the V/A folder coalescing pass.

After the folder-bounded album-ID change, a folder of compilation
tracks where each track carries its own per-album tag (e.g. a Jamendo
playlist) still fragments into one album per track. ``coalesce_va_folders``
collapses those into a single ``various_artists``-anchored album.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

import pytest

from kalinka_plugin_localfiles.indexer.indexer import (
    FileIndexer,
    VA_MIN_DISTINCT_ARTISTS,
)
from kalinka_plugin_localfiles.utils.id_generator import (
    generate_album_id,
    generate_artist_id,
    generate_track_id,
    generate_va_album_id,
)
from kalinka_plugin_localfiles.utils.name_utils import album_folder_for_path


class FakeDb:
    """Minimal in-memory stand-in for AsyncIndexerDb.

    Only the methods ``coalesce_va_folders`` actually calls are
    implemented. Test setup populates ``tracks`` / ``albums`` /
    ``artists`` directly to construct the scenario being asserted.
    """

    def __init__(self) -> None:
        self.tracks: Dict[str, Dict] = {}
        self.albums: Dict[str, Dict] = {}
        self.artists: Dict[str, Dict] = {}
        # Always seed the sentinel artists, mirroring db_schema.init_db.
        self.artists["unknown_artist"] = {"id": "unknown_artist", "name": "Unknown Artist"}
        self.artists["various_artists"] = {"id": "various_artists", "name": "Various Artists"}

    async def get_all_tracks(self) -> List[Dict]:
        return [dict(t) for t in self.tracks.values()]

    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        return dict(self.albums[album_id]) if album_id in self.albums else None

    async def insert_album(self, data: Dict) -> None:
        self.albums[data["id"]] = dict(data)

    async def update_track(self, track_id: str, data: Dict) -> None:
        if track_id in self.tracks:
            self.tracks[track_id].update(data)

    async def update_album_stats(self, album_id: str) -> None:
        if album_id not in self.albums:
            return
        tracks_in = [t for t in self.tracks.values() if t["album_id"] == album_id]
        self.albums[album_id]["track_count"] = len(tracks_in)
        self.albums[album_id]["duration"] = sum(
            t.get("duration") or 0 for t in tracks_in
        )

    async def delete_orphaned_albums_and_artists(self) -> Tuple[int, int]:
        live_album_ids = {t["album_id"] for t in self.tracks.values()}
        orphan_albums = [
            aid
            for aid in list(self.albums)
            if aid not in live_album_ids and aid != "unknown_album"
        ]
        for aid in orphan_albums:
            del self.albums[aid]
        live_artist_ids = {t["artist_id"] for t in self.tracks.values()}
        live_artist_ids |= {a["artist_id"] for a in self.albums.values()}
        orphan_artists = [
            aid
            for aid in list(self.artists)
            if aid not in live_artist_ids
            and aid != "unknown_artist"
            and aid != "various_artists"
        ]
        for aid in orphan_artists:
            del self.artists[aid]
        return len(orphan_albums), len(orphan_artists)


def _make_indexer(db: FakeDb) -> FileIndexer:
    """Build a FileIndexer with the fake DB. Bypasses __init__ because
    real init resolves music_folders against the filesystem and we don't
    need it here."""
    indexer = FileIndexer.__new__(FileIndexer)
    indexer.db_manager = db  # type: ignore[assignment]
    indexer.music_folders = []
    indexer.artwork_path = None  # type: ignore[assignment]
    return indexer


def _seed_track(
    db: FakeDb,
    file_path: str,
    artist_name: str,
    album_title: str,
    duration: int = 180,
) -> Tuple[str, str, str]:
    """Insert a track + its (artist, album) rows using the production
    ID schemes. Returns (track_id, artist_id, album_id)."""
    artist_id = generate_artist_id(artist_name)
    folder = album_folder_for_path(file_path)
    album_id = generate_album_id(album_title, folder)
    track_id = generate_track_id(file_path)

    if artist_id not in db.artists:
        db.artists[artist_id] = {"id": artist_id, "name": artist_name}
    if album_id not in db.albums:
        db.albums[album_id] = {
            "id": album_id,
            "title": album_title,
            "artist_id": artist_id,
            "track_count": 0,
            "duration": 0,
            "last_updated": int(time.time()),
        }
    db.tracks[track_id] = {
        "id": track_id,
        "title": "track",
        "file_path": file_path,
        "artist_id": artist_id,
        "album_id": album_id,
        "duration": duration,
        "last_updated": int(time.time()),
    }
    return track_id, artist_id, album_id


# ---------------------------------------------------------------------------
# The coalescing cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jamendo_playlist_coalesces_into_one_va_album():
    """The motivating case: one folder, many single-track albums whose
    only thing in common is the parent directory. Should collapse to a
    single V/A album anchored to ``various_artists``."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Playlist - Compilation"
    for i in range(6):
        _seed_track(
            db,
            f"{folder}/{i:02d} - track.mp3",
            artist_name=f"Artist {i}",
            album_title=f"Album {i}",  # each track has its own album tag
        )

    assert len(db.albums) == 6

    result = await indexer.coalesce_va_folders()

    assert result["folders"] == 1
    assert result["tracks"] == 6
    # The 6 per-track albums get cleaned up; the new V/A album survives.
    assert result["orphans"] == 6
    assert len(db.albums) == 1

    va_id = generate_va_album_id(folder)
    assert va_id in db.albums
    assert db.albums[va_id]["artist_id"] == "various_artists"
    # All tracks now point at the V/A album.
    for t in db.tracks.values():
        assert t["album_id"] == va_id


@pytest.mark.asyncio
async def test_normal_album_is_not_coalesced():
    """A normal single-artist album must be left alone — 1 artist
    fails the distinct-artists threshold immediately."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Pink Floyd - Animals"
    for i in range(5):
        _seed_track(
            db, f"{folder}/{i:02d}.flac", artist_name="Pink Floyd", album_title="Animals"
        )

    result = await indexer.coalesce_va_folders()

    assert result["folders"] == 0
    assert result["tracks"] == 0
    assert len(db.albums) == 1


@pytest.mark.asyncio
async def test_mistagged_album_is_not_coalesced():
    """Abbey Road with two mistagged tracks: 17 tracks, 3 distinct
    artists. Fails uniqueness ratio (3/17 ≈ 0.18 < 0.5) so should NOT
    become V/A — the right fix is to clean up the bad tags, not
    re-anchor the album."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/The Beatles - Abbey Road"
    for i in range(15):
        _seed_track(
            db,
            f"{folder}/{i:02d}.flac",
            artist_name="The Beatles",
            album_title="Abbey Road",
        )
    # Two mistagged tracks
    _seed_track(
        db, f"{folder}/m1.flac", artist_name="Ofra Harnoy", album_title="Abbey Road"
    )
    _seed_track(
        db, f"{folder}/m2.flac", artist_name="Bob Nanna", album_title="Abbey Road"
    )

    result = await indexer.coalesce_va_folders()

    assert result["folders"] == 0  # not flipped to V/A


@pytest.mark.asyncio
async def test_compilation_with_shared_album_tag_gets_va_anchor():
    """The other V/A pattern: same folder, same album tag, but tracks
    span many distinct artists (typical "Now That's What I Call X"
    rip). After my folder-bounded change these already collapse into
    one album row; coalescing just flips the anchor artist to
    ``various_artists``."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Now Thats What I Call 2024"
    artists = ["Artist A", "Artist B", "Artist C", "Artist D", "Artist E"]
    for i, name in enumerate(artists):
        _seed_track(
            db,
            f"{folder}/{i:02d}.flac",
            artist_name=name,
            album_title="Now Thats What I Call 2024",
        )
    # All tracks share the same album row already (same folder + title).
    assert len({t["album_id"] for t in db.tracks.values()}) == 1
    original_anchor = next(iter(db.albums.values()))["artist_id"]
    assert original_anchor != "various_artists"  # picked up first-track's artist

    result = await indexer.coalesce_va_folders()

    assert result["folders"] == 1
    # The V/A album is a NEW row (under generate_va_album_id), not the
    # original one — that original is now orphaned and deleted.
    va_id = generate_va_album_id(folder)
    assert va_id in db.albums
    assert db.albums[va_id]["artist_id"] == "various_artists"
    assert result["orphans"] >= 1
    for t in db.tracks.values():
        assert t["album_id"] == va_id


@pytest.mark.asyncio
async def test_threshold_boundary_just_below_passes():
    """Folder with exactly VA_MIN_DISTINCT_ARTISTS - 1 distinct artists
    must not coalesce, even if the uniqueness ratio is 1.0."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Trio Compilation"
    for i in range(VA_MIN_DISTINCT_ARTISTS - 1):
        _seed_track(
            db, f"{folder}/{i:02d}.flac", artist_name=f"A{i}", album_title=f"T{i}"
        )

    result = await indexer.coalesce_va_folders()
    assert result["folders"] == 0


@pytest.mark.asyncio
async def test_disc_subdirs_share_va_album():
    """If a V/A compilation is split into Disc 1 / Disc 2 subdirs,
    ``album_folder_for_path`` already walks up, so all tracks
    should land in the SAME V/A album."""
    db = FakeDb()
    indexer = _make_indexer(db)

    parent = "/Music/Massive Compilation"
    for i in range(3):
        _seed_track(
            db,
            f"{parent}/Disc 1/{i:02d}.flac",
            artist_name=f"Artist {i}",
            album_title=f"A{i}",
        )
    for i in range(3):
        _seed_track(
            db,
            f"{parent}/Disc 2/{i:02d}.flac",
            artist_name=f"Artist {i+10}",
            album_title=f"B{i}",
        )

    result = await indexer.coalesce_va_folders()
    assert result["folders"] == 1
    va_id = generate_va_album_id(parent)  # parent, not the disc subdirs
    for t in db.tracks.values():
        assert t["album_id"] == va_id


@pytest.mark.asyncio
async def test_unknown_and_various_artists_dont_count_toward_threshold():
    """Sentinel artists shouldn't inflate the distinct-artist count."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Half Tagged"
    # One real artist, the rest sentinel — should NOT trigger V/A.
    _seed_track(db, f"{folder}/01.mp3", artist_name="Real Artist", album_title="T")
    for i in range(5):
        # Use the sentinel artist directly (bypass _seed_track).
        track_id = generate_track_id(f"{folder}/u{i}.mp3")
        db.tracks[track_id] = {
            "id": track_id,
            "title": "u",
            "file_path": f"{folder}/u{i}.mp3",
            "artist_id": "unknown_artist",
            "album_id": "unknown_album",
            "duration": 100,
        }

    result = await indexer.coalesce_va_folders()
    assert result["folders"] == 0

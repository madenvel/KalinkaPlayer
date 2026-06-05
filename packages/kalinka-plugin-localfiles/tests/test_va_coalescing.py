"""Tests for the V/A folder *detach* pass (formerly "coalescing").

After the folder-bounded album-ID change, a folder of compilation
tracks where each track carries its own per-album tag (e.g. a Jamendo
playlist) still fragments into one album per track. The detach pass
re-points every such track to the ``unknown_album`` sentinel so:

  * the per-track single-track albums get cleaned up as orphans
  * each track surfaces as a single under its real artist via the
    orphan-tracks fallback in the browse view

The earlier "coalesce into a Various-Artists umbrella" behaviour was
rejected because it broke artist navigation: a track's artist_id
correctly pointed at the real artist, but the album was anchored to
``various_artists``, so the artist page found no albums for them.
"""

from __future__ import annotations

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
)
from kalinka_plugin_localfiles.utils.name_utils import album_folder_for_path


class FakeDb:
    """Minimal in-memory stand-in for AsyncIndexerDb. Only the methods
    ``orphan_va_folder_tracks`` calls are implemented."""

    def __init__(self) -> None:
        self.tracks: Dict[str, Dict] = {}
        self.albums: Dict[str, Dict] = {}
        self.artists: Dict[str, Dict] = {}
        # Seed the only sentinel that survives the V/A rewrite.
        self.artists["unknown_artist"] = {"id": "unknown_artist", "name": "Unknown Artist"}
        self.albums["unknown_album"] = {
            "id": "unknown_album",
            "title": "Unknown Album",
            "artist_id": "unknown_artist",
        }

    async def get_all_tracks(self) -> List[Dict]:
        return [dict(t) for t in self.tracks.values()]

    async def get_album_by_id(self, album_id: str) -> Optional[Dict]:
        return dict(self.albums[album_id]) if album_id in self.albums else None

    async def get_artist_by_id(self, artist_id: str) -> Optional[Dict]:
        return dict(self.artists[artist_id]) if artist_id in self.artists else None

    async def insert_album(self, data: Dict) -> None:
        self.albums[data["id"]] = dict(data)

    async def insert_artist(self, data: Dict) -> None:
        self.artists[data["id"]] = dict(data)

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
            if aid not in live_artist_ids and aid != "unknown_artist"
        ]
        for aid in orphan_artists:
            del self.artists[aid]
        return len(orphan_albums), len(orphan_artists)


def _make_indexer(db: FakeDb) -> FileIndexer:
    """Build a FileIndexer bypassing the heavy __init__ (which
    resolves music_folders against the real filesystem)."""
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
# The detach cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jamendo_playlist_tracks_become_singles_under_real_artists():
    """Motivating case: one folder, many single-track albums each
    anchored to a different real artist. After detach, every track
    points at ``unknown_album``, every per-track album is gone, and
    no Various-Artists row is created — artist navigation now works
    via the orphan-tracks fallback."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Playlist - Compilation"
    real_artist_ids = []
    for i in range(6):
        _, ar_id, _ = _seed_track(
            db,
            f"{folder}/{i:02d} - track.mp3",
            artist_name=f"Artist {i}",
            album_title=f"Album {i}",
        )
        real_artist_ids.append(ar_id)

    # Pre-state: 6 per-track albums, 6 distinct artists.
    assert len(db.albums) == 1 + 6  # unknown_album + 6 per-track albums

    result = await indexer.orphan_va_folder_tracks()

    assert result["folders"] == 1
    assert result["tracks"] == 6
    assert result["orphans"] == 6
    # All tracks now point to unknown_album.
    for t in db.tracks.values():
        assert t["album_id"] == "unknown_album"
    # The per-track albums are gone; only unknown_album survives.
    assert set(db.albums) == {"unknown_album"}
    # No Various-Artists row was created.
    assert "various_artists" not in db.artists
    # The real artists are still present so their tracks can be browsed
    # under them via the orphan-tracks fallback.
    for ar_id in real_artist_ids:
        assert ar_id in db.artists


@pytest.mark.asyncio
async def test_rerun_on_already_detached_folder_is_a_noop():
    """The detach pass runs on every scan (~every 15 min). Once a V/A
    folder's tracks are detached, a second run must report no work:
    ``folders``/``tracks``/``orphans`` all zero. This is what keeps the
    indexer from re-announcing the same detach in the logs forever."""
    db = FakeDb()
    indexer = _make_indexer(db)

    folder = "/Music/Playlist - Compilation"
    for i in range(6):
        _seed_track(
            db,
            f"{folder}/{i:02d} - track.mp3",
            artist_name=f"Artist {i}",
            album_title=f"Album {i}",
        )

    first = await indexer.orphan_va_folder_tracks()
    assert first == {"folders": 1, "tracks": 6, "orphans": 6}

    second = await indexer.orphan_va_folder_tracks()
    assert second == {"folders": 0, "tracks": 0, "orphans": 0}
    # State is unchanged by the no-op second run.
    for t in db.tracks.values():
        assert t["album_id"] == "unknown_album"


@pytest.mark.asyncio
async def test_normal_album_is_not_detached():
    """A normal single-artist album must be left alone — only 1
    distinct real artist, fails the threshold immediately."""
    db = FakeDb()
    indexer = _make_indexer(db)
    folder = "/Music/Pink Floyd - Animals"
    for i in range(5):
        _seed_track(
            db, f"{folder}/{i:02d}.flac", artist_name="Pink Floyd", album_title="Animals"
        )

    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 0
    # Tracks still point at the original album.
    assert all(
        t["album_id"] != "unknown_album" for t in db.tracks.values()
    )


@pytest.mark.asyncio
async def test_mistagged_album_is_not_detached():
    """Abbey Road with two mistagged tracks: 17 tracks, 3 distinct
    artists. Uniqueness ratio 3/17 ≈ 0.18 fails the 0.5 floor →
    NOT detached. The fix is to clean up the bad tags, not to
    nuke the whole album."""
    db = FakeDb()
    indexer = _make_indexer(db)
    folder = "/Music/The Beatles - Abbey Road"
    for i in range(15):
        _seed_track(
            db, f"{folder}/{i:02d}.flac", artist_name="The Beatles", album_title="Abbey Road"
        )
    _seed_track(
        db, f"{folder}/m1.flac", artist_name="Ofra Harnoy", album_title="Abbey Road"
    )
    _seed_track(
        db, f"{folder}/m2.flac", artist_name="Bob Nanna", album_title="Abbey Road"
    )

    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 0


@pytest.mark.asyncio
async def test_real_compilation_album_with_shared_tag_also_detaches():
    """The other V/A pattern: same folder, same album tag, many
    artists (e.g., 'Now That's What I Call X'). After the folder-
    bounded change these already share one album row anchored to the
    first track's artist. Detach still kicks in — the tracks lose
    their album linkage and surface as singles under each real
    artist. The umbrella album disappears from the album list."""
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
    original_album_id = next(iter(db.tracks.values()))["album_id"]
    assert original_album_id in db.albums

    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 1
    # Tracks all moved to unknown_album, the umbrella album was orphaned and deleted.
    for t in db.tracks.values():
        assert t["album_id"] == "unknown_album"
    assert original_album_id not in db.albums


@pytest.mark.asyncio
async def test_threshold_boundary_just_below_does_not_detach():
    db = FakeDb()
    indexer = _make_indexer(db)
    folder = "/Music/Trio Compilation"
    for i in range(VA_MIN_DISTINCT_ARTISTS - 1):
        _seed_track(
            db, f"{folder}/{i:02d}.flac", artist_name=f"A{i}", album_title=f"T{i}"
        )
    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 0


@pytest.mark.asyncio
async def test_disc_subdirs_count_under_the_parent_folder():
    """A V/A compilation split into Disc 1 / Disc 2 subdirs should be
    detected as a single folder via ``album_folder_for_path`` which
    walks up disc subdirs."""
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
    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 1
    for t in db.tracks.values():
        assert t["album_id"] == "unknown_album"


@pytest.mark.asyncio
async def test_unknown_artist_does_not_count_toward_threshold():
    """``unknown_artist``-anchored tracks don't inflate the distinct-
    artist count, so a folder with one real artist plus several
    untagged tracks doesn't get flipped to V/A."""
    db = FakeDb()
    indexer = _make_indexer(db)
    folder = "/Music/Half Tagged"
    _seed_track(db, f"{folder}/01.mp3", artist_name="Real Artist", album_title="T")
    for i in range(5):
        track_id = generate_track_id(f"{folder}/u{i}.mp3")
        db.tracks[track_id] = {
            "id": track_id,
            "title": "u",
            "file_path": f"{folder}/u{i}.mp3",
            "artist_id": "unknown_artist",
            "album_id": "unknown_album",
            "duration": 100,
        }
    result = await indexer.orphan_va_folder_tracks()
    assert result["folders"] == 0

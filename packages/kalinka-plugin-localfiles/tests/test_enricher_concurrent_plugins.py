"""Plugin-side guards that concurrent enrichment depends on.

Serial enrichment was the implicit protection for per-plugin shared state.
With a batch of entities in flight at once, the MusicBrainz caches must
single-flight (or sibling tracks of one album each pay the fetch the cache
exists to avoid) and AcoustID's pacing state must be locked (or concurrent
lookups read one idle gap and all fire together).
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest

from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin
from kalinka_plugin_localfiles.enricher.musicbrainz_plugin import MusicBrainzPlugin


def _async_returning(value):
    async def _fn(*_a, **_k):
        return value

    return _fn


def _mb_plugin():
    config = MagicMock()
    config.enricher.plugins.musicbrainz.artist_threshold = 70
    config.enricher.plugins.musicbrainz.album_threshold = 70
    config.enricher.plugins.musicbrainz.track_threshold = 70
    config.enricher.plugins.musicbrainz.string_similarity = 0.6
    config.enricher.plugins.musicbrainz.debug_matching = False
    config.enricher.plugins.user_agent = "test/1.0 (test@example.com)"
    config.legacy_tag_encoding = ""
    return MusicBrainzPlugin(config, db_manager=MagicMock())


@pytest.mark.asyncio
async def test_release_detail_is_fetched_once_for_concurrent_tracks(monkeypatch):
    plugin = _mb_plugin()
    calls = 0

    def fake_get_release_by_id(mbid, includes=None):
        nonlocal calls
        calls += 1
        time.sleep(0.01)  # the MB round-trip, in its worker thread
        return {"release": {"id": mbid, "medium-list": []}}

    monkeypatch.setattr(
        "kalinka_plugin_localfiles.enricher.musicbrainz_plugin."
        "musicbrainzngs.get_release_by_id",
        fake_get_release_by_id,
    )

    results = await asyncio.gather(
        *(plugin._get_release_detail("rel-1") for _ in range(5))
    )

    assert calls == 1, "sibling tracks must share one release fetch"
    assert all(r["id"] == "rel-1" for r in results)


@pytest.mark.asyncio
async def test_accepted_release_candidate_is_queried_once_per_album():
    plugin = _mb_plugin()
    queries = 0

    async def slow_lookup(album_id):
        nonlocal queries
        queries += 1
        await asyncio.sleep(0.01)
        return None

    plugin.db_manager.get_accepted_release_candidate = slow_lookup

    tracks = [
        {"id": f"t{i}", "album_id": "al1", "track_number": i} for i in range(5)
    ]
    await asyncio.gather(*(plugin._lookup_track_in_accepted_map(t) for t in tracks))

    assert queries == 1, "one query per album, not per track"


class _ArtistCreatingDb:
    """Minimal db_manager for the filename fallback's artist creation, with
    the deterministic-ID insert semantics of the real one."""

    def __init__(self):
        self.artists: dict[str, dict] = {}
        self.inserts = 0

    async def search_artists(self, name, limit):
        await asyncio.sleep(0)  # a real query yields
        return list(self.artists.values()), len(self.artists)

    async def get_artist_by_id(self, artist_id):
        await asyncio.sleep(0)
        return self.artists.get(artist_id)

    async def insert_artist(self, data):
        self.inserts += 1
        self.artists[data["id"]] = data  # INSERT OR REPLACE


@pytest.mark.asyncio
async def test_concurrent_tracks_resolve_one_artist_row():
    """Tracks are enriched by a pool of workers, so several may name the same
    artist at once. The ID is a hash of the normalised name and the insert
    replaces by that ID, so the racing writers converge on one row.

    AcoustID is the last plugin that mints an artist from a track: the
    indexer creates them too, but processes files one at a time."""
    db = _ArtistCreatingDb()
    plugin = _acoustid_plugin()
    plugin.db_manager = db

    ids = await asyncio.gather(
        *(plugin._create_or_get_artist("В. Цой") for _ in range(5))
    )

    assert len(set(ids)) == 1
    assert len(db.artists) == 1
    assert next(iter(db.artists.values()))["name"] == "В. Цой"


def _acoustid_plugin():
    config = MagicMock()
    config.enricher.plugins.acoustid.api_key = "key"
    return AcoustIdPlugin(config, db_manager=MagicMock())


def test_rate_limit_serialises_across_threads():
    """The pacing interval must hold when several worker threads call it —
    AcoustID lookups run via ``asyncio.to_thread``, so without the lock they
    all read the same idle ``last_request_time`` and burst together."""
    plugin = _acoustid_plugin()
    plugin.request_interval = 0.02

    timestamps: list[float] = []
    lock = threading.Lock()

    def call():
        plugin._wait_for_rate_limit()
        with lock:
            timestamps.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(4)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(timestamps) == 4
    # Four paced calls span at least three intervals; a racing implementation
    # returns them all at once, well under that.
    assert time.monotonic() - start >= 3 * plugin.request_interval * 0.9

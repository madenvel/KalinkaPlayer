"""The enricher processes a batch of entities concurrently.

Phases stay ordered (artists feed albums feed tracks), but entities within a
phase are independent, so they overlap — that is what keeps MusicBrainz's
1 req/s slot busy instead of idle between entities. Failure semantics must
survive the change: siblings finish their work, and a transient failure still
ends the pass leaving its own row pending.
"""

from __future__ import annotations

import asyncio

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    EnrichmentStatus,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
)
from kalinka_plugin_localfiles.enricher.keyed_lock import KeyedLock


class FakeDb:
    """Serves pending tracks and retires them as they are written."""

    def __init__(self, tracks):
        self.pending = list(tracks)
        self.saved = {}

    async def get_non_enriched_artists(self, limit=1):
        return []

    async def get_non_enriched_albums(self, limit=1):
        return []

    async def get_non_enriched_tracks(self, limit=1):
        return self.pending[:limit]

    async def update_track(self, eid, data):
        self.saved[eid] = data.get("enriched")
        self.pending = [t for t in self.pending if t["id"] != eid]

    async def update_album_stats(self, *_a, **_k):
        pass

    async def record_claim(self, *_a, **_k):
        pass

    async def get_resolved_origin(self, *_a, **_k):
        return None

    async def record_resolved_origin(self, *_a, **_k):
        pass


class OverlapPlugin:
    runs_after_resolution = False

    """Records how many enrichments are in flight at once."""

    def __init__(self, delay=0.02):
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    def can_enrich_artist(self):
        return False

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return True

    async def enrich_track(self, track):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        return None


def _track(n):
    return {
        "id": f"t{n}",
        "title": f"Song {n}",
        "artist_id": "ar1",
        "album_id": "al1",
        "duration": 100,
        "track_number": n,
    }


def _enricher(db, plugins, concurrency):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = db
    enr.plugins = plugins
    enr.entity_concurrency = concurrency
    return enr


@pytest.mark.asyncio
async def test_batch_entities_are_enriched_concurrently():
    db = FakeDb([_track(i) for i in range(1, 7)])
    plugin = OverlapPlugin()
    enr = _enricher(db, [plugin], concurrency=3)

    result = await enr._process_tracks()

    assert result.processed == 6
    assert plugin.max_in_flight == 3, "batch should overlap up to the limit"
    assert all(v == EnrichmentStatus.ENRICHED for v in db.saved.values())


@pytest.mark.asyncio
async def test_concurrency_of_one_keeps_the_serial_behaviour():
    db = FakeDb([_track(i) for i in range(1, 4)])
    plugin = OverlapPlugin()
    enr = _enricher(db, [plugin], concurrency=1)

    await enr._process_tracks()

    assert plugin.max_in_flight == 1


class FlakyPlugin(OverlapPlugin):
    runs_after_resolution = False

    """Fails one nominated track transiently; the rest succeed."""

    def __init__(self, failing_id):
        super().__init__()
        self.failing_id = failing_id
        self.completed = []

    async def enrich_track(self, track):
        await asyncio.sleep(0.01)
        if track["id"] == self.failing_id:
            raise TransientEnrichmentError("MusicBrainz is unreachable")
        self.completed.append(track["id"])
        return None


@pytest.mark.asyncio
async def test_transient_failure_ends_the_pass_and_keeps_its_row_pending():
    db = FakeDb([_track(i) for i in range(1, 5)])
    plugin = FlakyPlugin(failing_id="t2")
    enr = _enricher(db, [plugin], concurrency=4)

    # run_enrichment swallows the transient and ends the pass.
    await enr.run_enrichment()

    # The failing row was never written, so the next cycle retries it...
    assert "t2" not in db.saved
    assert any(t["id"] == "t2" for t in db.pending)
    # ...while its siblings' completed work was still saved.
    assert set(db.saved) == {"t1", "t3", "t4"}
    assert set(plugin.completed) == {"t1", "t3", "t4"}


@pytest.mark.asyncio
async def test_keyed_lock_single_flights_concurrent_callers():
    lock = KeyedLock()
    fetches = 0
    cache = {}

    async def get(key):
        nonlocal fetches
        if key in cache:
            return cache[key]
        async with lock(key):
            if key in cache:
                return cache[key]
            fetches += 1
            await asyncio.sleep(0.01)  # the "fetch"
            cache[key] = f"value-{key}"
            return cache[key]

    results = await asyncio.gather(*(get("release-1") for _ in range(5)))

    assert results == ["value-release-1"] * 5
    assert fetches == 1, "siblings must wait for the first fetch, not repeat it"

    # Distinct keys are independent.
    await asyncio.gather(get("release-2"), get("release-3"))
    assert fetches == 3


def test_keyed_lock_returns_one_lock_per_key():
    lock = KeyedLock()
    assert lock("a") is lock("a")
    assert lock("a") is not lock("b")

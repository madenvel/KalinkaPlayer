#!/usr/bin/env python3
"""Phase 2e: ENRICHED is redefined as "local identity resolved", not "all
external fields present". An entity whose genuinely-local required fields
resolve is ENRICHED even with no mbid/cover/year/genre — ENRICHED (local-only),
not FAILED. Only a missing *required local* field is a FAILURE.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    EnrichmentStatus,
    MetadataEnricher,
)


class FakeDb:
    def __init__(self):
        self.saved = {}          # id -> enriched status written

    async def update_artist(self, eid, data):
        self.saved[eid] = data.get("enriched")

    async def update_album(self, eid, data):
        self.saved[eid] = data.get("enriched")

    async def update_track(self, eid, data):
        self.saved[eid] = data.get("enriched")

    async def update_album_stats(self, *_a, **_k):
        pass

    async def record_claim(self, *_a, **_k):
        pass

    async def record_resolved_origin(self, *_a, **_k):
        pass

    async def get_album_track_tags(self, *_a, **_k):
        return []


def _enricher():
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    enr.plugins = []             # no external plugins: local-only resolution
    return enr


@pytest.mark.asyncio
async def test_album_local_only_is_enriched_not_failed():
    enr = _enricher()
    # title + artist_id present, but no mbid / cover / year / genre.
    await enr._enrich_album({"id": "al1", "title": "Bootleg", "artist_id": "ar1"})
    assert enr.db_manager.saved["al1"] == EnrichmentStatus.ENRICHED


@pytest.mark.asyncio
async def test_album_missing_required_local_field_fails():
    enr = _enricher()
    # no title -> a required local field is missing -> FAILED.
    await enr._enrich_album({"id": "al2", "artist_id": "ar1"})
    assert enr.db_manager.saved["al2"] == EnrichmentStatus.FAILED


@pytest.mark.asyncio
async def test_artist_name_only_is_enriched():
    enr = _enricher()
    await enr._enrich_artist({"id": "ar1", "name": "Some Artist"})
    assert enr.db_manager.saved["ar1"] == EnrichmentStatus.ENRICHED


@pytest.mark.asyncio
async def test_track_local_only_is_enriched():
    enr = _enricher()
    await enr._enrich_track({
        "id": "t1", "title": "Song", "artist_id": "ar1", "album_id": "al1",
        "duration": 180, "track_number": 3,
    })  # no mbid
    assert enr.db_manager.saved["t1"] == EnrichmentStatus.ENRICHED


@pytest.mark.asyncio
async def test_track_missing_track_number_fails():
    enr = _enricher()
    await enr._enrich_track({
        "id": "t2", "title": "Song", "artist_id": "ar1", "album_id": "al1",
        "duration": 180,  # no track_number -> required local field missing
    })
    assert enr.db_manager.saved["t2"] == EnrichmentStatus.FAILED

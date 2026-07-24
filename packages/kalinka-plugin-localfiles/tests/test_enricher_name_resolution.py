#!/usr/bin/env python3
"""Phase 2d, first slice: the enricher resolves the artist display name from
claims. A MusicBrainz match re-cases a matching local name ("THE BEATLES" ->
"The Beatles") but never replaces a genuinely different one, and the decision's
provenance is recorded.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher import MetadataEnricher


class FakeDb:
    def __init__(self):
        self.claims = []
        self.origins = []

    async def record_claim(self, et, eid, field, value, source, tier):
        self.claims.append((et, eid, field, value, source, tier))

    async def record_resolved_origin(self, et, eid, field, source, tier, ev=None):
        self.origins.append((et, eid, field, source, tier))


def _enricher():
    # Skip __init__ (which builds the real plugin stack); we only exercise
    # _resolve_display_field against a fake db.
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    return enr


MB_CLAIM = {
    "field": "name",
    "value": "The Beatles",
    "source": "musicbrainz:mbid-1",
    "tier": "inferred",
}


@pytest.mark.asyncio
async def test_recases_name_from_matching_mb_claim():
    enr = _enricher()
    artist = {"id": "a1", "name": "THE BEATLES"}
    updated = dict(artist)
    changed = await enr._resolve_display_field("artist", artist, updated, [MB_CLAIM], "name")
    assert changed is True
    assert updated["name"] == "The Beatles"
    # Claim persisted and origin recorded as the MB source.
    assert enr.db_manager.claims[0][:4] == ("artist", "a1", "name", "The Beatles")
    assert enr.db_manager.origins[0] == (
        "artist", "a1", "name", "musicbrainz:mbid-1", "inferred",
    )


@pytest.mark.asyncio
async def test_keeps_local_name_when_mb_differs():
    enr = _enricher()
    artist = {"id": "a1", "name": "The Beatles"}
    updated = dict(artist)
    diff = {**MB_CLAIM, "value": "The Beetles"}  # different artist
    changed = await enr._resolve_display_field("artist", artist, updated, [diff], "name")
    assert changed is False
    assert updated["name"] == "The Beatles"


@pytest.mark.asyncio
async def test_no_name_claim_keeps_local_but_records_origin():
    enr = _enricher()
    artist = {"id": "a1", "name": "THE BEATLES"}
    updated = dict(artist)
    changed = await enr._resolve_display_field("artist", artist, updated, [], "name")
    assert changed is False
    assert updated["name"] == "THE BEATLES"
    assert enr.db_manager.claims == []
    # Provenance is still recorded for the uncontested local value.
    assert enr.db_manager.origins[0] == (
        "artist", "a1", "name", "tag_consensus", "observed",
    )

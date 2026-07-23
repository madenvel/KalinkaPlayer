#!/usr/bin/env python3
"""Phase 2d, second slice: the enricher resolves the album display title from
claims. A MusicBrainz release match re-cases a matching local title ("ABBEY
ROAD" -> "Abbey Road") but never replaces a genuinely different one, and the
decision's provenance is recorded.
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
    # _resolve_album_title against a fake db.
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    return enr


MB_CLAIM = {
    "field": "title",
    "value": "Abbey Road",
    "source": "musicbrainz:rel-1",
    "tier": "inferred",
}


@pytest.mark.asyncio
async def test_recases_title_from_matching_mb_claim():
    enr = _enricher()
    album = {"id": "al1", "title": "ABBEY ROAD"}
    updated = dict(album)
    changed = await enr._resolve_album_title(album, updated, [MB_CLAIM])
    assert changed is True
    assert updated["title"] == "Abbey Road"
    assert enr.db_manager.claims[0][:4] == ("album", "al1", "title", "Abbey Road")
    assert enr.db_manager.origins[0] == (
        "album", "al1", "title", "musicbrainz:rel-1", "inferred",
    )


@pytest.mark.asyncio
async def test_keeps_local_title_when_mb_differs():
    enr = _enricher()
    album = {"id": "al1", "title": "The Singles - The First Ten Years"}
    updated = dict(album)
    diff = {**MB_CLAIM, "value": "Gold: Greatest Hits"}  # different release
    changed = await enr._resolve_album_title(album, updated, [diff])
    assert changed is False
    assert updated["title"] == "The Singles - The First Ten Years"


@pytest.mark.asyncio
async def test_no_title_claim_keeps_local_but_records_origin():
    enr = _enricher()
    album = {"id": "al1", "title": "Oxygène"}
    updated = dict(album)
    changed = await enr._resolve_album_title(album, updated, [])
    assert changed is False
    assert updated["title"] == "Oxygène"
    assert enr.db_manager.claims == []
    assert enr.db_manager.origins[0] == (
        "album", "al1", "title", "tag_consensus", "observed",
    )


@pytest.mark.asyncio
async def test_missing_local_title_is_a_noop():
    enr = _enricher()
    album = {"id": "al1"}
    changed = await enr._resolve_album_title(album, dict(album), [MB_CLAIM])
    assert changed is False
    assert enr.db_manager.claims == [] and enr.db_manager.origins == []

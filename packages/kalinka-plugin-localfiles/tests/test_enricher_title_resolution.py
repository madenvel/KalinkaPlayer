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
    # _resolve_display_field against a fake db.
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
    changed = await enr._resolve_display_field("album", album, updated, [MB_CLAIM], "title")
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
    changed = await enr._resolve_display_field("album", album, updated, [diff], "title")
    assert changed is False
    assert updated["title"] == "The Singles - The First Ten Years"


@pytest.mark.asyncio
async def test_no_title_claim_keeps_local_but_records_origin():
    enr = _enricher()
    album = {"id": "al1", "title": "Oxygène"}
    updated = dict(album)
    changed = await enr._resolve_display_field("album", album, updated, [], "title")
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
    changed = await enr._resolve_display_field("album", album, dict(album), [MB_CLAIM], "title")
    assert changed is False
    assert enr.db_manager.claims == [] and enr.db_manager.origins == []


@pytest.mark.asyncio
async def test_track_title_recased_from_acoustid_claim():
    """The generic resolver serves tracks too (Phase: track-path claims): an
    AcoustID rescue title re-cases a matching local track title, and provenance
    is recorded against the track entity."""
    enr = _enricher()
    track = {"id": "t1", "title": "some song"}
    updated = dict(track)
    ac_claim = {"field": "title", "value": "Some Song",
                "source": "acoustid:rec-1", "tier": "inferred"}
    changed = await enr._resolve_display_field(
        "track", track, updated, [ac_claim], "title"
    )
    assert changed is True
    assert updated["title"] == "Some Song"
    assert enr.db_manager.origins[0] == (
        "track", "t1", "title", "acoustid:rec-1", "inferred",
    )


@pytest.mark.asyncio
async def test_track_title_kept_when_claim_differs():
    enr = _enricher()
    track = {"id": "t1", "title": "Real Title"}
    updated = dict(track)
    diff = {"field": "title", "value": "Wrong Title",
            "source": "acoustid:rec-1", "tier": "inferred"}
    changed = await enr._resolve_display_field(
        "track", track, updated, [diff], "title"
    )
    assert changed is False
    assert updated["title"] == "Real Title"


@pytest.mark.asyncio
async def test_plugin_refined_title_survives_resolution():
    """Regression: resolution must read the local baseline from the working
    copy, not the stale row. An untagged rip's row title is the raw basename;
    FilesystemFallbackPlugin parses it into a real title mid-pass, and
    resolving against the row value used to revert that parse."""
    enr = _enricher()
    basename = "THE BEATLES - 01.Come Together (Lennon-McCartney).flac"
    track = {"id": "t1", "title": basename}
    updated = {**track, "title": "Come Together (Lennon-McCartney)"}
    changed = await enr._resolve_display_field("track", track, updated, [], "title")
    assert changed is False
    assert updated["title"] == "Come Together (Lennon-McCartney)"
    assert enr.db_manager.origins[0] == (
        "track", "t1", "title", "tag_consensus", "observed",
    )


@pytest.mark.asyncio
async def test_refined_title_not_reverted_by_differing_claim():
    """A differing external claim must not restore the basename either — the
    refined working-copy title is the local value the §7 rule protects."""
    enr = _enricher()
    track = {"id": "t1", "title": "ARTIST - 02.Something.flac"}
    updated = {**track, "title": "Something"}
    diff = {"field": "title", "value": "Something (Remastered)",
            "source": "musicbrainz:rec-9", "tier": "inferred"}
    changed = await enr._resolve_display_field("track", track, updated, [diff], "title")
    assert changed is False
    assert updated["title"] == "Something"

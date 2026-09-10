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

    origin = None

    async def get_resolved_origin(self, _et, _eid, _field):
        return self.origin

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
async def test_a_refinement_made_this_pass_survives_resolution():
    """Regression: resolution reads the local baseline from the working copy,
    not the stale row. An album title loses its artist prefix earlier in the
    same pass, and resolving against the row value used to restore it."""
    enr = _enricher()
    album = {"id": "al1", "title": "The Beatles - Abbey Road"}
    updated = {**album, "title": "Abbey Road"}
    changed = await enr._resolve_display_field("album", album, updated, [], "title")
    assert changed is False
    assert updated["title"] == "Abbey Road"
    assert enr.db_manager.origins[0] == (
        "album", "al1", "title", "tag_consensus", "observed",
    )


@pytest.mark.asyncio
async def test_a_refinement_is_not_reverted_by_a_differing_claim():
    """A differing external claim must not restore the prefixed title either
    — the refined working-copy value is what the §7 rule protects."""
    enr = _enricher()
    album = {"id": "al1", "title": "Pink Floyd - The Wall"}
    updated = {**album, "title": "The Wall"}
    diff = {"field": "title", "value": "The Wall (Remastered)",
            "source": "musicbrainz:rel-9", "tier": "inferred"}
    changed = await enr._resolve_display_field("album", album, updated, [diff], "title")
    assert changed is False
    assert updated["title"] == "The Wall"


@pytest.mark.asyncio
async def test_a_title_only_the_path_supplied_is_corrected_instead():
    """The same claim wins once the stored origin says the local title was
    never more than a reading of the folder name."""
    enr = _enricher()
    enr.db_manager.origin = {"source": "folder_name", "tier": "guessed"}
    album = {"id": "al1", "title": "The Wall"}
    updated = dict(album)
    diff = {"field": "title", "value": "The Wall (Remastered)",
            "source": "musicbrainz:rel-9", "tier": "inferred"}
    changed = await enr._resolve_display_field("album", album, updated, [diff], "title")
    assert changed is True
    assert updated["title"] == "The Wall (Remastered)"

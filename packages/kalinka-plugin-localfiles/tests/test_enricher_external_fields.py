#!/usr/bin/env python3
"""Phase 2d, slice 3: external-first origin/era fields resolved from claims.

A fuzzy MusicBrainz match is `inferred`; a local file tag is `observed`. So
for genre/year/language the local tag wins when present, while country/area/
original_year (no competing local tag) are filled by MB. Provenance for the
winner is always recorded.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    ALBUM_EXTERNAL_FIELDS,
    ARTIST_EXTERNAL_FIELDS,
    MetadataEnricher,
)


class FakeDb:
    def __init__(self):
        self.claims = []
        self.origins = []

    async def record_claim(self, et, eid, field, value, source, tier):
        self.claims.append((et, eid, field, value, source, tier))

    async def record_resolved_origin(self, et, eid, field, source, tier, ev=None):
        self.origins.append((et, eid, field, source, tier))


def _enricher():
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    return enr


def _mb(field, value, mbid="rel-1"):
    return {"field": field, "value": value, "source": f"musicbrainz:{mbid}",
            "tier": "inferred"}


@pytest.mark.asyncio
async def test_local_tag_genre_beats_fuzzy_mb():
    enr = _enricher()
    album = {"id": "al1", "genre": "Krautrock"}   # from file tags (observed)
    updated = {**album, "genre": "Electronic"}    # MB already overwrote via update
    changed = await enr._resolve_external_fields(
        "album", album, updated, [_mb("genre", "Electronic")], ALBUM_EXTERNAL_FIELDS
    )
    assert changed is True
    assert updated["genre"] == "Krautrock"        # local tag wins back
    # origin recorded as the local source
    assert ("album", "al1", "genre", "tag_consensus", "observed") in enr.db_manager.origins


@pytest.mark.asyncio
async def test_mb_fills_original_year_uncontested():
    enr = _enricher()
    album = {"id": "al1"}                          # no local original_year
    updated = {"id": "al1", "original_year": 1979}  # MB update
    changed = await enr._resolve_external_fields(
        "album", album, updated, [_mb("original_year", 1979)], ALBUM_EXTERNAL_FIELDS
    )
    # winner == already-set MB value, so no *change*, but provenance recorded.
    assert changed is False
    assert updated["original_year"] == 1979
    assert ("album", "al1", "original_year", "musicbrainz:rel-1", "inferred") \
        in enr.db_manager.origins


@pytest.mark.asyncio
async def test_local_year_beats_mb_year():
    enr = _enricher()
    album = {"id": "al1", "year": 1984}            # tag year (release pressing)
    updated = {**album, "year": 1990}              # MB reissue year
    changed = await enr._resolve_external_fields(
        "album", album, updated, [_mb("year", 1990)], ALBUM_EXTERNAL_FIELDS
    )
    assert changed is True
    assert updated["year"] == 1984


@pytest.mark.asyncio
async def test_artist_country_filled_uncontested():
    enr = _enricher()
    artist = {"id": "ar1"}
    updated = {"id": "ar1", "country": "IT"}
    changed = await enr._resolve_external_fields(
        "artist", artist, updated, [_mb("country", "IT", mbid="a-1")],
        ARTIST_EXTERNAL_FIELDS,
    )
    assert changed is False
    assert updated["country"] == "IT"
    assert ("artist", "ar1", "country", "musicbrainz:a-1", "inferred") \
        in enr.db_manager.origins


@pytest.mark.asyncio
async def test_no_claims_for_field_is_skipped():
    enr = _enricher()
    album = {"id": "al1"}                           # no local, no external
    updated = {"id": "al1"}
    changed = await enr._resolve_external_fields(
        "album", album, updated, [], ALBUM_EXTERNAL_FIELDS
    )
    assert changed is False
    assert enr.db_manager.claims == [] and enr.db_manager.origins == []

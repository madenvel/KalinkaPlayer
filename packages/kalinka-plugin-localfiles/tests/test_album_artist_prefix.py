#!/usr/bin/env python3
"""An untagged rip's album title is folder-derived and keeps the artist prefix
("The Beatles - Abbey Road") until clustering learns the artist. Searching a
provider with that stale title fails, and by then the album is already enriched
— so the enricher strips it before matching, where the artist is known.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher import MetadataEnricher


class FakeDb:
    def __init__(self):
        self.saved = {}

    async def update_album(self, eid, data):
        self.saved[eid] = data

    async def record_claim(self, *_a, **_k):
        pass

    async def record_resolved_origin(self, *_a, **_k):
        pass

    async def get_album_track_tags(self, *_a, **_k):
        return []


def _enricher():
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    enr.plugins = []
    return enr


@pytest.mark.asyncio
async def test_prefix_stripped_before_matching():
    enr = _enricher()
    album = {"id": "al1", "title": "The Beatles - Abbey Road",
             "artist_id": "ar1", "artist_name": "The Beatles"}
    updated = dict(album)
    assert await enr._strip_album_artist_prefix(album, updated) is True
    assert updated["title"] == "Abbey Road"


@pytest.mark.asyncio
async def test_eponymous_album_keeps_its_name():
    """"Boston - Boston" must not become empty."""
    enr = _enricher()
    album = {"id": "al1", "title": "Boston", "artist_name": "Boston"}
    updated = dict(album)
    assert await enr._strip_album_artist_prefix(album, updated) is False
    assert updated["title"] == "Boston"


@pytest.mark.asyncio
async def test_unrelated_title_untouched():
    enr = _enricher()
    album = {"id": "al1", "title": "Abbey Road", "artist_name": "The Beatles"}
    updated = dict(album)
    assert await enr._strip_album_artist_prefix(album, updated) is False
    assert updated["title"] == "Abbey Road"


@pytest.mark.asyncio
async def test_no_partial_word_match():
    """"AB" must not corrupt "ABBA Gold"."""
    enr = _enricher()
    album = {"id": "al1", "title": "ABBA Gold", "artist_name": "AB"}
    updated = dict(album)
    assert await enr._strip_album_artist_prefix(album, updated) is False
    assert updated["title"] == "ABBA Gold"


@pytest.mark.asyncio
async def test_unknown_artist_is_a_noop():
    enr = _enricher()
    album = {"id": "al1", "title": "The Beatles - Abbey Road"}
    updated = dict(album)
    assert await enr._strip_album_artist_prefix(album, updated) is False


@pytest.mark.asyncio
async def test_enrich_album_persists_the_cleaned_title():
    enr = _enricher()
    await enr._enrich_album({
        "id": "al1", "title": "The Beatles - Abbey Road",
        "artist_id": "ar1", "artist_name": "The Beatles",
    })
    assert enr.db_manager.saved["al1"]["title"] == "Abbey Road"

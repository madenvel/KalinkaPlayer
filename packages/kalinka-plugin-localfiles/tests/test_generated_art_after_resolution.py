#!/usr/bin/env python3
"""Art is drawn from the album's resolved identity, not the guess it started as.

The generator derives its image from title, artist and genre, all of which
resolution can still change — a folder-derived title especially, now that
MusicBrainz is allowed to correct one. Drawing inside the fetch chain would
key deterministic art to a name that changes a moment later, and the sweep
that clears generated art only re-opens FAILED rows, so a corrected album
would keep the wrong cover for good.
"""

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    EnrichmentStatus,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.procedural_artwork_plugin import (
    ProceduralArtworkPlugin,
)


class FakeDb:
    async def record_claim(self, *_a, **_k):
        return None

    async def get_resolved_origin(self, _et, _eid, _field):
        return {"source": "folder_name", "tier": "guessed"}

    async def record_resolved_origin(self, *_a, **_k):
        return None

    async def get_album_track_tags(self, *_a, **_k):
        return []

    async def update_album(self, *_a, **_k):
        return None


class _Recorder:
    """Notes the album title it was shown."""

    runs_after_resolution = False

    def __init__(self):
        self.saw = None

    def can_enrich_artist(self):
        return False

    def can_enrich_album(self):
        return True

    def can_enrich_track(self):
        return False

    async def enrich_album(self, album):
        self.saw = album["title"]
        return None


class _Fetcher(_Recorder):
    """Stands in for MusicBrainz: claims the canonical title."""

    async def enrich_album(self, album):
        self.saw = album["title"]
        return {
            "updates": {"mbid": "rel-1"},
            "claims": [{
                "field": "title",
                "value": "Abbey Road",
                "source": "musicbrainz:rel-1",
                "tier": "inferred",
            }],
        }


class _Deriver(_Recorder):
    runs_after_resolution = True


def _enricher(plugins):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    enr.plugins = plugins
    enr._backoff_instance = None
    return enr


@pytest.mark.asyncio
async def test_a_deriving_plugin_sees_the_resolved_title():
    fetcher, deriver = _Fetcher(), _Deriver()
    enr = _enricher([fetcher, deriver])
    # A folder-derived title MusicBrainz is about to correct.
    await enr._enrich_album(
        {"id": "al1", "title": "The Beatles - ABBEY ROAD", "artist_id": "ar1",
         "enriched": EnrichmentStatus.NOT_ENRICHED}
    )
    assert fetcher.saw == "The Beatles - ABBEY ROAD"
    assert deriver.saw == "Abbey Road"


def test_the_artwork_generator_is_one_of_them():
    assert ProceduralArtworkPlugin.runs_after_resolution is True

#!/usr/bin/env python3
"""The order of the pipeline, asserted where it is decided.

Tags first (the indexer), then the metadata sources, then the fingerprint for
whatever they could not identify, then artwork. AcoustID's position is the
load-bearing one: reading the audio is the expensive answer, so it is asked
last — and the chain's early break is what turns "last" into "only when the
cheaper sources found no mbid".
"""

import pytest

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.enricher import (
    TRACK_DESIRED_FIELDS,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_db import AsyncEnricherDb


def _all_sources(tmp_path) -> LocalFilesConfig:
    cfg = LocalFilesConfig(
        db_path=str(tmp_path / "test.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    cfg.enricher.plugins.acoustid.api_key = "key"
    cfg.enricher.plugins.musicbrainz.enabled = True
    cfg.enricher.plugins.wikidata.enabled = True
    cfg.enricher.plugins.deezer.enabled = True
    cfg.enricher.plugins.coverartarchive.enabled = True
    cfg.enricher.plugins.procedural_artwork.enabled = True
    return cfg


def test_identity_sources_run_before_artwork_and_acoustid_runs_last(tmp_path):
    enricher = MetadataEnricher(_all_sources(tmp_path), AsyncEnricherDb(
        _all_sources(tmp_path)
    ))
    names = [p.__class__.__name__ for p in enricher.plugins]
    assert names[:4] == [
        "MusicBrainzPlugin",
        "WikidataPlugin",
        "DeezerPlugin",
        "AcoustIdPlugin",
    ]
    # Artwork brings up the rear; the generator is last of all, and only
    # after resolution (see test_generated_art_after_resolution).
    assert names[-1] == "ProceduralArtworkPlugin"
    assert "CoverArtArchivePlugin" in names[4:]


def test_an_mbid_is_what_the_chain_stops_on():
    """"AcoustID only when nothing else identified it" is not a gate in the
    plugin — it falls out of the chain's completion test, which counts the
    external id among the fields a track wants."""
    assert "mbid" in TRACK_DESIRED_FIELDS


class _Source:
    runs_after_resolution = False

    def __init__(self, result=None):
        self.result = result
        self.called = False

    def can_enrich_artist(self):
        return False

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return True

    async def enrich_track(self, track):
        self.called = True
        return self.result


def _enricher(plugins):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.plugins = plugins
    return enr


COMPLETE = {f: "x" for f in TRACK_DESIRED_FIELDS}


@pytest.mark.asyncio
async def test_a_track_the_earlier_sources_identify_never_reaches_the_last():
    finder = _Source({"updates": dict(COMPLETE)})
    last = _Source()
    enr = _enricher([finder, last])
    await enr._run_plugin_chain(
        "t1", {"id": "t1"}, [],
        lambda p: p.can_enrich_track(),
        lambda p, e: p.enrich_track(e),
        lambda e: all(e.get(f) for f in TRACK_DESIRED_FIELDS),
    )
    assert finder.called and not last.called


@pytest.mark.asyncio
async def test_a_track_they_cannot_identify_does_reach_it():
    finder = _Source({"updates": {"title": "Named, but unidentified"}})
    last = _Source()
    enr = _enricher([finder, last])
    await enr._run_plugin_chain(
        "t1", {"id": "t1"}, [],
        lambda p: p.can_enrich_track(),
        lambda p, e: p.enrich_track(e),
        lambda e: all(e.get(f) for f in TRACK_DESIRED_FIELDS),
    )
    assert finder.called and last.called

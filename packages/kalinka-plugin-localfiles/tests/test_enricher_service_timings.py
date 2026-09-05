"""A pass must report which source spent its time.

Finding the slow source by disabling plugins one at a time does not work:
the chain stops early once an entity has every desired field, so removing
one plugin changes how often the others run. The chain measures instead.
"""

from __future__ import annotations

import logging

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    EnrichmentStatus,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
)
from kalinka_plugin_localfiles.enricher.service_timings import ServiceTimings


class FakeDb:
    def __init__(self, artists):
        self.pending = list(artists)

    async def get_non_enriched_artists(self, limit=1):
        return self.pending[:limit]

    async def get_non_enriched_albums(self, limit=1):
        return []

    async def get_non_enriched_tracks(self, limit=1):
        return []

    async def update_artist(self, eid, data):
        if data.get("enriched") in (
            EnrichmentStatus.ENRICHED,
            EnrichmentStatus.FAILED,
        ):
            self.pending = [a for a in self.pending if a["id"] != eid]

    async def record_claim(self, *_a, **_k):
        pass

    async def record_resolved_origin(self, *_a, **_k):
        pass


class SlowService:
    """Answers, but only after the clock has moved a long way."""

    def __init__(self, clock, cost):
        self._clock = clock
        self._cost = cost

    def can_enrich_artist(self):
        return True

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return False

    async def enrich_artist(self, artist):
        self._clock[0] += self._cost
        return {"updates": {"image_url": f"img-{artist['id']}"}}


class TimingOutService(SlowService):
    """Burns time and then reports the service unreachable."""

    async def enrich_artist(self, artist):
        self._clock[0] += self._cost
        raise TransientEnrichmentError("MusicBrainz is unreachable")


def _enricher(db, plugins, clock):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = db
    enr.plugins = plugins
    enr.entity_concurrency = 1
    enr.deferred_rows = 0
    enr.__dict__["_service_timings"] = ServiceTimings(clock=lambda: clock[0])
    return enr


class TestServiceTimings:
    def test_calls_and_seconds_accumulate_per_service(self):
        clock = [0.0]
        t = ServiceTimings(clock=lambda: clock[0])

        with t.measure("mb"):
            clock[0] += 3.0
        with t.measure("mb"):
            clock[0] += 1.0
        with t.measure("deezer"):
            clock[0] += 0.5

        assert t.summary() == [("mb", 2, 4.0), ("deezer", 1, 0.5)]
        assert t.total_seconds == pytest.approx(4.5)

    def test_a_raising_call_is_still_timed(self):
        """A source that costs a minute to time out is the one worth
        finding, so its cost must not vanish with the exception."""
        clock = [0.0]
        t = ServiceTimings(clock=lambda: clock[0])

        with pytest.raises(TransientEnrichmentError):
            with t.measure("mb"):
                clock[0] += 56.0
                raise TransientEnrichmentError("unreachable")

        assert t.summary() == [("mb", 1, 56.0)]

    def test_summary_is_worst_first(self):
        clock = [0.0]
        t = ServiceTimings(clock=lambda: clock[0])
        for service, cost in (("cheap", 1.0), ("dear", 9.0), ("mid", 4.0)):
            with t.measure(service):
                clock[0] += cost

        assert [row[0] for row in t.summary()] == ["dear", "mid", "cheap"]

    def test_empty_until_something_is_measured(self):
        t = ServiceTimings()
        assert not t
        assert t.summary() == []
        assert t.total_seconds == 0.0


@pytest.mark.asyncio
async def test_pass_attributes_its_time_to_the_slow_source(caplog):
    clock = [0.0]
    db = FakeDb([{"id": "ar1", "name": "Artist 1"}])
    enr = _enricher(
        db, [SlowService(clock, 9.0), SlowService(clock, 1.0)], clock
    )

    with caplog.at_level(logging.INFO, logger="enricher"):
        await enr.run_enrichment()

    report = [r for r in caplog.messages if "time by source" in r]
    assert report, "the pass must report where its time went"
    assert "(artists)" in report[0] and "SlowService 10s/2 call(s)" in report[0]


@pytest.mark.asyncio
async def test_time_lost_to_an_unreachable_service_is_reported(caplog):
    """The stand-down log says a service failed; this says what it cost."""
    clock = [0.0]
    db = FakeDb([{"id": "ar1", "name": "Artist 1"}])
    enr = _enricher(
        db, [TimingOutService(clock, 56.0), SlowService(clock, 1.0)], clock
    )

    with caplog.at_level(logging.INFO, logger="enricher"):
        await enr.run_enrichment()

    report = [r for r in caplog.messages if "time by source" in r]
    assert "TimingOutService 56s/1 call(s) (98%" in report[0]


@pytest.mark.asyncio
async def test_an_idle_pass_reports_nothing(caplog):
    """No rows, no report — the log stays quiet on an enriched library."""
    enr = _enricher(FakeDb([]), [], [0.0])

    with caplog.at_level(logging.INFO, logger="enricher"):
        await enr.run_enrichment()

    assert not [r for r in caplog.messages if "time by source" in r]

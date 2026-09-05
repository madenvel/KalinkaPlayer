"""A service outage must not stop the pipeline.

A transient failure says nothing about the entity, so the row keeps
NOT_ENRICHED — but the pass carries on: other sources still get their turn on
that same row, other rows keep enriching, and the failing service is stood
down for a growing interval instead of being re-discovered once per row.
The rows left waiting are retried on that interval, not on the library scan
(15 minutes), which used to be the only thing that restarted a pass.
"""

from __future__ import annotations

import asyncio

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    EnrichmentStatus,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    TransientEnrichmentError,
)
from kalinka_plugin_localfiles.enricher.service_backoff import ServiceBackoff


class FakeDb:
    def __init__(self, artists):
        self.pending = list(artists)
        self.saved: dict[str, dict] = {}

    async def get_non_enriched_artists(self, limit=1):
        return self.pending[:limit]

    async def get_non_enriched_albums(self, limit=1):
        return []

    async def get_non_enriched_tracks(self, limit=1):
        return []

    async def update_artist(self, eid, data):
        self.saved[eid] = dict(data)
        if data.get("enriched") in (
            EnrichmentStatus.ENRICHED,
            EnrichmentStatus.FAILED,
        ):
            self.pending = [a for a in self.pending if a["id"] != eid]

    async def record_claim(self, *_a, **_k):
        pass

    async def record_resolved_origin(self, *_a, **_k):
        pass


class DownService:
    """Never answers."""

    def __init__(self):
        self.calls = 0

    def can_enrich_artist(self):
        return True

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return False

    async def enrich_artist(self, artist):
        self.calls += 1
        raise TransientEnrichmentError("MusicBrainz is unreachable")


class WorkingService:
    """Answers, filling one field."""

    def __init__(self):
        self.seen: list[str] = []

    def can_enrich_artist(self):
        return True

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return False

    async def enrich_artist(self, artist):
        self.seen.append(artist["id"])
        return {"updates": {"image_url": f"img-{artist['id']}"}}


def _enricher(db, plugins, concurrency=1):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = db
    enr.plugins = plugins
    enr.entity_concurrency = concurrency
    enr.deferred_rows = 0
    return enr


def _artists(n):
    return [{"id": f"ar{i}", "name": f"Artist {i}"} for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_outage_does_not_stop_the_pass():
    """The old behaviour ended the whole pass on the first transient and
    waited for the next scan; every remaining row must now still be tried."""
    db = FakeDb(_artists(5))
    down, working = DownService(), WorkingService()
    enr = _enricher(db, [down, working])

    await enr.run_enrichment()

    # Every row was attempted, not just the first.
    assert working.seen == ["ar1", "ar2", "ar3", "ar4", "ar5"]


@pytest.mark.asyncio
async def test_other_sources_still_enrich_the_same_row():
    """A dead MusicBrainz says nothing about whether Deezer can answer."""
    db = FakeDb(_artists(1))
    enr = _enricher(db, [DownService(), WorkingService()])

    await enr.run_enrichment()

    saved = db.saved["ar1"]
    assert saved["image_url"] == "img-ar1"          # the working source ran
    assert saved.get("enriched") != EnrichmentStatus.ENRICHED
    assert saved.get("enriched") != EnrichmentStatus.FAILED


@pytest.mark.asyncio
async def test_deferred_row_keeps_no_verdict_and_stays_pending():
    """Recording local-only here would close the row for good on a passing
    outage, so it must stay NOT_ENRICHED for a later retry."""
    db = FakeDb(_artists(1))
    enr = _enricher(db, [DownService()])

    await enr.run_enrichment()

    assert db.pending, "row must remain pending"
    assert enr.deferred_rows == 1
    # Nothing was written, or if written, no terminal status.
    assert db.saved.get("ar1", {}).get("enriched") in (None, EnrichmentStatus.NOT_ENRICHED)


@pytest.mark.asyncio
async def test_failing_service_is_probed_once_not_once_per_row():
    """Without a stand-down, an outage costs one failed request (and its
    internal retries) per row — the thing that made a bad MusicBrainz day
    grind the library to a halt."""
    db = FakeDb(_artists(20))
    down = DownService()
    enr = _enricher(db, [down, WorkingService()])

    await enr.run_enrichment()

    assert down.calls == 1, "the outage should be discovered once per pass"


@pytest.mark.asyncio
async def test_pass_terminates_instead_of_reserving_deferred_rows_forever():
    """Deferred rows keep no status, so a phase that re-fetched them would
    loop forever serving the same rows."""
    db = FakeDb(_artists(3))
    enr = _enricher(db, [DownService()])

    await asyncio.wait_for(enr.run_enrichment(), timeout=5)

    assert enr.deferred_rows == 3


@pytest.mark.asyncio
async def test_retry_delay_is_offered_while_rows_wait():
    """The worker loop wakes on this instead of the 15-minute scan tick."""
    db = FakeDb(_artists(2))
    enr = _enricher(db, [DownService()])

    assert enr.next_retry_delay() is None  # nothing waiting yet

    await enr.run_enrichment()

    delay = enr.next_retry_delay()
    assert delay is not None and 0 < delay <= 60


@pytest.mark.asyncio
async def test_recovered_service_enriches_normally_again():
    """When the service comes back, rows complete without a restart."""

    class FlakyOnce:
        def __init__(self):
            self.calls = 0

        def can_enrich_artist(self):
            return True

        def can_enrich_album(self):
            return False

        def can_enrich_track(self):
            return False

        async def enrich_artist(self, artist):
            self.calls += 1
            if self.calls == 1:
                raise TransientEnrichmentError("blip")
            return {"updates": {"mbid": "mbid-1", "image_url": "img"}}

    db = FakeDb(_artists(1))
    flaky = FlakyOnce()
    enr = _enricher(db, [flaky])

    await enr.run_enrichment()
    assert db.pending, "still pending after the blip"

    # The cooldown elapses, and the retry pass completes the row.
    enr.__dict__["_service_backoff"] = ServiceBackoff(base_delay=0.0)
    await enr.run_enrichment()

    assert db.saved["ar1"]["enriched"] == EnrichmentStatus.ENRICHED
    assert not db.pending


class TestServiceBackoff:
    def test_cooldown_doubles_while_the_service_stays_down(self):
        clock = [0.0]
        b = ServiceBackoff(base_delay=10.0, max_delay=40.0, clock=lambda: clock[0])

        assert b.record_failure("mb") == 10.0
        assert b.record_failure("mb") == 20.0
        assert b.record_failure("mb") == 40.0
        assert b.record_failure("mb") == 40.0, "capped"

    def test_cooling_expires_and_success_resets_escalation(self):
        clock = [0.0]
        b = ServiceBackoff(base_delay=10.0, clock=lambda: clock[0])

        b.record_failure("mb")
        assert b.is_cooling("mb")
        clock[0] = 9.9
        assert b.is_cooling("mb")
        clock[0] = 10.0
        assert not b.is_cooling("mb")

        b.record_success("mb")
        assert b.record_failure("mb") == 10.0, "escalation reset after success"

    def test_next_retry_in_reports_the_soonest_service(self):
        clock = [0.0]
        b = ServiceBackoff(base_delay=10.0, clock=lambda: clock[0])
        assert b.next_retry_in() is None

        b.record_failure("slow")
        b.record_failure("slow")   # 20s
        b.record_failure("quick")  # 10s
        assert b.next_retry_in() == pytest.approx(10.0)

        clock[0] = 5.0
        assert b.next_retry_in() == pytest.approx(5.0)

    def test_services_cool_independently(self):
        b = ServiceBackoff(base_delay=10.0, clock=lambda: 0.0)
        b.record_failure("mb")
        assert b.is_cooling("mb")
        assert not b.is_cooling("deezer")

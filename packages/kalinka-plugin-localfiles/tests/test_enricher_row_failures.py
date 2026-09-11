#!/usr/bin/env python3
"""A source that fails on one row, against one that has nothing to say.

Both used to look identical to the chain — a plugin returned None either way
— so a failure silently handed the field to the next source, which guessed
from a name, and the row was then closed as complete. Which picture an
artist ended up with depended on whether an API happened to be up, and the
row stayed wrong for as long as nothing re-opened it.

A failure now keeps the row pending so the source that was entitled to answer
can answer on a later pass. The service keeps its turn on every other row,
because one bad row says nothing about whether the service is well; and a row
that fails every time is not held back for ever, or the library would never
finish.
"""

from __future__ import annotations

import pytest

from kalinka_plugin_localfiles.enricher.enricher import (
    MAX_DEFERRALS,
    MetadataEnricher,
)
from kalinka_plugin_localfiles.enricher.enricher_plugin import (
    EntityEnrichmentError,
    TransientEnrichmentError,
)


class FakeDb:
    def __init__(self):
        self.saved: dict[str, dict] = {}

    async def update_artist(self, eid, data):
        self.saved[eid] = dict(data)

    async def record_claim(self, *_a, **_k):
        pass

    async def get_resolved_origin(self, *_a, **_k):
        return None

    async def record_resolved_origin(self, *_a, **_k):
        pass


class _Plugin:
    runs_after_resolution = False

    def can_enrich_artist(self):
        return True

    def can_enrich_album(self):
        return False

    def can_enrich_track(self):
        return False


class RowFails(_Plugin):
    """Answers for every artist but one, which it cannot process."""

    def __init__(self, bad_id="ar1"):
        self.bad_id = bad_id
        self.seen: list[str] = []

    async def enrich_artist(self, artist):
        self.seen.append(artist["id"])
        if artist["id"] == self.bad_id:
            raise EntityEnrichmentError("its picture would not decode")
        return {"updates": {"image_url": f"img-{artist['id']}"}}


class Guesser(_Plugin):
    """The next source along, which fills the same field from a name."""

    def __init__(self):
        self.seen: list[str] = []

    async def enrich_artist(self, artist):
        self.seen.append(artist["id"])
        return {"updates": {"image_url": f"guessed-{artist['id']}"}}


def _enricher(plugins):
    enr = MetadataEnricher.__new__(MetadataEnricher)
    enr.db_manager = FakeDb()
    enr.plugins = plugins
    enr.entity_concurrency = 1
    enr.deferred_rows = 0
    return enr


async def _chain(enr, artist):
    return await enr._run_plugin_chain(
        artist["id"],
        artist,
        [],
        lambda p: p.can_enrich_artist(),
        lambda p, e: p.enrich_artist(e),
        lambda e: bool(e.get("image_url")),
    )


class TestAFailedRow:
    @pytest.mark.asyncio
    async def test_the_row_is_kept_pending(self):
        enr = _enricher([RowFails()])

        _, deferred = await _chain(enr, {"id": "ar1", "name": "Vangelis"})

        assert deferred is True

    @pytest.mark.asyncio
    async def test_a_later_source_does_not_fill_what_the_failure_left(self):
        """The whole defect: the guesser got its turn and the row closed, so
        the source that failed never had another chance at it."""
        guesser = Guesser()
        enr = _enricher([RowFails(), guesser])
        artist = {"id": "ar1", "name": "Vangelis"}

        _, deferred = await _chain(enr, artist)

        assert deferred is True
        assert guesser.seen == ["ar1"]  # it still gets its turn ...
        assert artist.get("image_url") == "guessed-ar1"
        # ... but the row stays pending, so the failed source is asked again.

    @pytest.mark.asyncio
    async def test_the_service_keeps_its_turn_on_every_other_row(self):
        """One bad row is not an outage; standing the source down would cost
        every other artist the answer it could have given."""
        failing = RowFails(bad_id="ar1")
        enr = _enricher([failing])

        await _chain(enr, {"id": "ar1", "name": "Vangelis"})
        _, deferred = await _chain(enr, {"id": "ar2", "name": "Kino"})

        assert deferred is False
        assert failing.seen == ["ar1", "ar2"]
        assert not enr._backoff.is_cooling("RowFails")


class TestAnOutageStillStandsTheServiceDown:
    @pytest.mark.asyncio
    async def test_a_transient_failure_still_cools_the_service(self):
        """The distinction must not blur: an unreachable service should not
        be re-discovered once per row."""

        class Down(_Plugin):
            async def enrich_artist(self, artist):
                raise TransientEnrichmentError("unreachable")

        enr = _enricher([Down()])

        _, deferred = await _chain(enr, {"id": "ar1", "name": "Vangelis"})

        assert deferred is True
        assert enr._backoff.is_cooling("Down")


class TestTheCeilingOnWaiting:
    @pytest.mark.asyncio
    async def test_a_row_that_always_fails_eventually_takes_a_verdict(self):
        """Waiting for ever would leave the library permanently unfinished."""
        enr = _enricher([RowFails()])
        artist = {"id": "ar1", "name": "Vangelis"}

        results = [(await _chain(enr, dict(artist)))[1] for _ in range(MAX_DEFERRALS + 1)]

        assert results[:MAX_DEFERRALS] == [True] * MAX_DEFERRALS
        assert results[MAX_DEFERRALS] is False

    @pytest.mark.asyncio
    async def test_a_row_that_recovers_starts_counting_again(self):
        """The count is about consecutive failures, so a row that succeeds
        must not carry its history into some later outage."""
        failing = RowFails()
        enr = _enricher([failing])

        await _chain(enr, {"id": "ar1", "name": "Vangelis"})
        assert enr._deferrals["ar1"] == 1

        failing.bad_id = "other"
        await _chain(enr, {"id": "ar1", "name": "Vangelis"})

        assert "ar1" not in enr._deferrals

    @pytest.mark.asyncio
    async def test_a_row_reopened_later_gets_its_full_allowance_again(self):
        """Only rows still being waited on are counted, so a row the sweep
        re-opens is not born already out of patience."""
        enr = _enricher([RowFails()])

        for _ in range(MAX_DEFERRALS + 1):
            await _chain(enr, {"id": "ar1", "name": "Vangelis"})
        assert "ar1" not in enr._deferrals

        _, deferred = await _chain(enr, {"id": "ar1", "name": "Vangelis"})

        assert deferred is True

    @pytest.mark.asyncio
    async def test_one_row_running_out_does_not_spend_anothers_patience(self):
        enr = _enricher([RowFails(bad_id="ar1")])

        for _ in range(MAX_DEFERRALS + 1):
            await _chain(enr, {"id": "ar1", "name": "Vangelis"})
        failing = enr.plugins[0]
        failing.bad_id = "ar2"
        _, deferred = await _chain(enr, {"id": "ar2", "name": "Kino"})

        assert deferred is True


class TestAnAlreadyIdentifiedArtist:
    @pytest.mark.asyncio
    async def test_musicbrainz_does_not_search_for_an_id_it_already_has(
        self, tmp_path
    ):
        """Re-opening an artist for another source used to cost a MusicBrainz
        search — a request a second — to rediscover the id on the row."""
        from kalinka_plugin_localfiles.config_model import LocalFilesConfig
        from kalinka_plugin_localfiles.enricher.musicbrainz_plugin import (
            MusicBrainzPlugin,
        )

        config = LocalFilesConfig(
            db_path=str(tmp_path / "db.sqlite"),
            artwork_path=str(tmp_path / "artwork"),
        )
        plugin = MusicBrainzPlugin(config, db_manager=None)

        async def fail(*_a, **_k):
            raise AssertionError("MusicBrainz was asked to search")

        plugin_module = __import__(
            "kalinka_plugin_localfiles.enricher.musicbrainz_plugin",
            fromlist=["mb_call"],
        )
        original, plugin_module.mb_call = plugin_module.mb_call, fail
        try:
            result = await plugin.enrich_artist(
                {"id": "ar1", "name": "Vangelis", "mbid": "57fca0e2"}
            )
        finally:
            plugin_module.mb_call = original

        assert result is None

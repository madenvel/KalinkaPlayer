"""MusicBrainz pacing: request *starts* are spaced, round-trips overlap.

musicbrainzngs' own limiter holds a lock across the whole request, so
concurrent callers run strictly one at a time and pay pacing + latency each.
``mb_client`` disables it and spaces the starts instead, which is what makes
concurrent enrichment worth anything: N calls cost about N intervals, not N
× (interval + latency).
"""

from __future__ import annotations

import asyncio
import threading
import time

import musicbrainzngs
import pytest

from kalinka_plugin_localfiles.enricher import mb_client
from kalinka_plugin_localfiles.enricher.mb_client import mb_call


@pytest.fixture
def paced(monkeypatch):
    """A short but real interval, and a fresh slot clock per test."""
    monkeypatch.setattr(mb_client._pacer, "_min_interval", 0.05)
    monkeypatch.setattr(mb_client._pacer, "_next_slot", 0.0)
    return 0.05


def test_library_rate_limiting_is_disabled():
    """Ours replaces it; leaving both on would serialise every request
    behind the library's lock again."""
    assert musicbrainzngs.musicbrainz.do_rate_limit is False


@pytest.mark.asyncio
async def test_starts_are_spaced(paced):
    starts: list[float] = []

    def record():
        starts.append(time.monotonic())

    await asyncio.gather(*(mb_call(record) for _ in range(5)))

    assert len(starts) == 5
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= paced * 0.8 for g in gaps), gaps


@pytest.mark.asyncio
async def test_slow_requests_overlap(paced):
    """A slow response must not push the next request's start out — that
    serialisation is exactly what the library's limiter did."""
    latency = 0.3

    def slow():
        time.sleep(latency)

    started = time.monotonic()
    await asyncio.gather(*(mb_call(slow) for _ in range(4)))
    elapsed = time.monotonic() - started

    # Serialised this would be 4 × (interval + latency) ≈ 1.4 s; paced with
    # overlap it is bounded by the last start plus one latency.
    assert elapsed < 3 * paced + latency + 0.25, elapsed


@pytest.mark.asyncio
async def test_waiting_for_a_permit_does_not_bank_a_slot(paced, monkeypatch):
    """A caller must reserve its slot after it gets a permit, not before.

    Slots taken while every permit is held elapse unused in the queue, so
    when two slow calls finish at the same instant their waiters start
    together — a burst straight through the one-per-second budget.
    """
    monkeypatch.setattr(mb_client._pacer, "_max_in_flight", 2)
    monkeypatch.setattr(mb_client._pacer, "_loop", None)  # rebuild the semaphore
    starts: list[float] = []
    # The first two occupy both permits and finish together.
    latencies = iter([0.30, 0.25])
    lock = threading.Lock()

    def occupy():
        with lock:
            latency = next(latencies, 0.0)
            starts.append(time.monotonic())
        time.sleep(latency)

    await asyncio.gather(*(mb_call(occupy) for _ in range(6)))

    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= paced * 0.8 for g in gaps), gaps


def test_survives_a_change_of_event_loop(monkeypatch):
    """The lock and semaphore belong to the loop that first contends on
    them; a later loop must get its own rather than "bound to a different
    event loop"."""
    monkeypatch.setattr(mb_client._pacer, "_min_interval", 0.001)

    def noop():
        pass

    async def burst():
        await asyncio.gather(*(mb_call(noop) for _ in range(6)))

    asyncio.run(burst())
    asyncio.run(burst())


@pytest.mark.asyncio
async def test_exceptions_propagate_unchanged(paced):
    """The plugins classify MB errors as verdicts or transient failures, so
    the wrapper must not wrap or swallow them."""

    def boom():
        raise musicbrainzngs.NetworkError("down")

    with pytest.raises(musicbrainzngs.NetworkError):
        await mb_call(boom)


@pytest.mark.asyncio
async def test_arguments_are_forwarded(paced):
    def echo(*args, **kwargs):
        return args, kwargs

    assert await mb_call(echo, "a", limit=20) == (("a",), {"limit": 20})

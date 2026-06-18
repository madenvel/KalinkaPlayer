import asyncio

import pytest

from kalinka_serialized import ResolutionSlot


@pytest.mark.asyncio
async def test_start_runs_and_finishes():
    """start() runs the coroutine; once it finishes the slot goes idle."""
    slot = ResolutionSlot()
    applied = []

    async def op(gen):
        if slot.is_current(gen):
            applied.append(gen)
            slot.finish(gen)

    gen = slot.start(op, target=3)
    assert slot.active
    assert slot.target == 3

    await asyncio.sleep(0)  # let the task run

    assert applied == [gen]
    assert not slot.active
    assert slot.target is None


@pytest.mark.asyncio
async def test_start_supersedes_and_cancels_previous():
    """A second start() cancels the first task and invalidates its generation."""
    slot = ResolutionSlot()
    cancelled = asyncio.Event()

    async def first(gen):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    g1 = slot.start(first, target=1)
    await asyncio.sleep(0)  # let `first` park on the wait

    g2 = slot.start(lambda gen: asyncio.sleep(0), target=2)

    assert g2 != g1
    assert not slot.is_current(g1)
    assert slot.is_current(g2)

    await asyncio.sleep(0)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_superseded_continuation_does_not_apply():
    """A continuation guarded by is_current() applies only for the latest op."""
    slot = ResolutionSlot()
    applied = []
    release = asyncio.Event()

    async def op(gen, tag):
        await release.wait()
        if not slot.is_current(gen):
            return
        applied.append(tag)
        slot.finish(gen)

    slot.start(lambda gen: op(gen, "first"), target=1)
    await asyncio.sleep(0)
    slot.start(lambda gen: op(gen, "second"), target=2)

    release.set()
    await asyncio.sleep(0.01)

    assert applied == ["second"]


@pytest.mark.asyncio
async def test_cancel_if():
    """cancel_if supersedes only when the predicate returns True for the target."""
    slot = ResolutionSlot()

    async def idle(gen):
        await asyncio.Event().wait()

    slot.start(idle, target=5)
    assert slot.active

    slot.cancel_if(lambda target: target == 99)  # predicate sees target=5 → False
    assert slot.active

    slot.cancel_if(lambda target: target == 5)  # True → supersede
    assert not slot.active
    assert slot.target is None


def test_cancel_if_when_idle_does_not_call_predicate():
    slot = ResolutionSlot()
    # No in-flight op: predicate must not run (there is no target to pass).
    slot.cancel_if(lambda target: pytest.fail("predicate called while idle"))
    assert not slot.active


def test_supersede_when_idle_is_safe():
    slot = ResolutionSlot()
    slot.supersede()  # no in-flight task
    assert not slot.active
    assert slot.target is None


@pytest.mark.asyncio
async def test_finish_with_stale_gen_does_not_clear_newer():
    """A stale continuation calling finish() must not clear a newer op's task."""
    slot = ResolutionSlot()

    async def idle(gen):
        await asyncio.Event().wait()

    g1 = slot.start(idle, target=1)
    slot.start(idle, target=2)  # supersedes g1

    slot.finish(g1)  # stale → no-op

    assert slot.active  # g2's task is still held
    assert slot.target == 2

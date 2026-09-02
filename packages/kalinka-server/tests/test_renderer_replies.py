"""Waiting for a renderer's answer across a reconnect.

A renderer drops and comes back on a new socket while a request is still out
on the old one. Nothing riding the new link may be settled — answered or
failed — by the socket it replaced.
"""

import asyncio

import pytest

from kalinka_server.renderer_registry import RendererUnavailable
from kalinka_server.renderer_replies import PendingReplies


class FakeLink:
    """Reserves ids like a real connection and never answers on its own."""

    def __init__(self):
        self._id = 0

    def next_message_id(self) -> int:
        self._id += 1
        return self._id


async def _in_flight(pending: PendingReplies, link: FakeLink) -> asyncio.Task:
    async def send(_message_id: int) -> None:
        return None

    task = asyncio.ensure_future(pending.request("rid-1", link, send))
    await asyncio.sleep(0)
    return task


async def test_the_reply_reaches_the_link_it_was_asked_on():
    pending = PendingReplies("config", timeout_s=1.0)
    old, new = FakeLink(), FakeLink()
    waiting = await _in_flight(pending, new)

    # Both links hand out id 1, so only the link tells the two waits apart.
    pending.handle_reply("rid-1", old, 1, "from the old socket")
    assert not waiting.done()

    pending.handle_reply("rid-1", new, 1, "from the new socket")
    assert await waiting == "from the new socket"


async def test_a_replaced_socket_does_not_fail_the_new_one():
    pending = PendingReplies("upgrade", timeout_s=1.0)
    old, new = FakeLink(), FakeLink()
    waiting = await _in_flight(pending, new)

    pending.handle_disconnect("rid-1", old)
    assert not waiting.done()

    pending.handle_disconnect("rid-1", new)
    with pytest.raises(RendererUnavailable):
        await waiting


async def test_a_link_says_whether_it_is_already_being_asked():
    pending = PendingReplies("upgrade", timeout_s=1.0)
    link = FakeLink()
    assert not pending.waiting_on(link)

    waiting = await _in_flight(pending, link)
    assert pending.waiting_on(link)

    pending.handle_reply("rid-1", link, 1, "done")
    await waiting
    assert not pending.waiting_on(link)

"""Fire-and-forget work, kept referenced until it finishes."""

from __future__ import annotations

import asyncio

# asyncio holds only a weak reference to a running task.
_running: set[asyncio.Task] = set()


def detach(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task

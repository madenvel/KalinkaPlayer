"""Shared utilities for long-lived subprocess workers."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import queue
from typing import Optional

logger = logging.getLogger(__name__.split(".")[-1])


async def sleep_interruptible(
    duration: float,
    shutdown_event: asyncio.Event,
    nudge_queue: Optional[multiprocessing.Queue],
    label: str = "Worker",
) -> bool:
    """Sleep for *duration* seconds, waking early on shutdown or nudge.

    Returns True if woken by a nudge, False otherwise.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    while not shutdown_event.is_set():
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        if nudge_queue is not None:
            try:
                nudge_queue.get_nowait()
                logger.info("%s woken by nudge", label)
                return True
            except queue.Empty:
                pass
        await asyncio.sleep(min(1.0, remaining))
    return False

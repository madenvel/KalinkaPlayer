"""Shared utilities for long-lived subprocess workers."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import multiprocessing
import queue
from typing import Optional

logger = logging.getLogger(__name__.split(".")[-1])


# Linux prctl op for setting the kernel-level process name (visible as
# COMM in `ps -o comm`). Kernel truncates to 15 chars + NUL.
_PR_SET_NAME = 15


def set_proc_title(name: str) -> None:
    """Set the running process's ``comm`` (kernel-level name) so it
    shows up distinctly in ``ps`` / ``htop`` / ``top``.

    Without this, multiprocessing-spawned children inherit the parent's
    command line and command name, so ``ps -o comm`` shows five
    identical ``kalinka-server`` rows and it's impossible to tell at a
    glance which one is hot.

    Best-effort: silently no-ops on non-Linux or if libc isn't
    available — process naming is purely a diagnostic affordance and
    a failure here must never block worker startup.
    """
    if not name:
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # Linux truncates to TASK_COMM_LEN-1 = 15 bytes anyway, but
        # being explicit avoids passing the rest into kernel space.
        libc.prctl(_PR_SET_NAME, name.encode("utf-8")[:15], 0, 0, 0)
    except Exception:  # noqa: BLE001 — diagnostic only
        pass


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
                logger.debug("%s woken by nudge", label)
                return True
            except queue.Empty:
                pass
        await asyncio.sleep(min(1.0, remaining))
    return False

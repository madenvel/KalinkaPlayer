"""The speaker test: a short tone played through a renderer.

The tone is a FLAC file the server ships and hosts under :data:`TONE_ROUTE`;
the renderer fetches and decodes it like any other source, so this module is
only about who holds the renderer while it sounds.

The tone gets its own session rather than borrowing the play queue's. Sharing
one would feed the tone's own SOURCE_CHANGED and FINISHED back into the queue,
which reads them as its track ending and advances the list. So playback on the
target renderer is stopped first, deliberately and visibly, and the tone's
session is closed as soon as the tone ends — the queue can claim the renderer
again straight after.

Two tones arrive per test, `left` then `right` two seconds apart. The second
reuses the first's session, where set_source replaces what is sounding.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .renderer_registry import RendererRegistry
from .renderer_sessions import (
    CloseReason,
    PlaybackSession,
    SessionPool,
    SessionState,
)
from .renderer_state import StateChange
from .stream_state import AudioGraphNodeState, from_snapshot
from .tasks import detach

logger = logging.getLogger(__name__.split(".")[-1])

TONE_FREQUENCY_HZ = 440
# The length of the shipped files (scripts/make_test_tones.sh).
TONE_DURATION_MS = 3000
CHANNELS = ("left", "right", "both")

TONE_DIR = Path(__file__).resolve().parent / "assets" / "tones"
TONE_ROUTE = "/server/tones"
TONE_MOUNT_NAME = "test-tones"
TONE_MIME_TYPE = "audio/flac"

# Backstop for a renderer that never reports the end of the tone; the session
# is normally closed by the FINISHED that follows it.
_HOLD_S = TONE_DURATION_MS / 1000 + 2.0

_DONE_STATES = (
    AudioGraphNodeState.FINISHED,
    AudioGraphNodeState.STOPPED,
    AudioGraphNodeState.ERROR,
)


def tone_channel(requested: str) -> str:
    """The channel a caller asked for, or `both` when it is not one we serve."""
    channel = requested.lower()
    return channel if channel in CHANNELS else "both"


def tone_filename(channel: str) -> str:
    """The shipped file for a channel, relative to :data:`TONE_ROUTE`."""
    return f"{channel}.flac"


def tone_uri(channel: str) -> str:
    """The renderer's in-process generator, which nothing sends any more. Kept
    because renderers still accept it and it needs nothing served."""
    return (
        f"tone://{channel}?freq={TONE_FREQUENCY_HZ}"
        f"&duration_ms={TONE_DURATION_MS}"
    )


class TonePlayer:
    """Plays test tones on renderers, one session at a time.

    `release_playback` asks whoever is playing to give a renderer up and
    returns once it has.
    """

    def __init__(
        self,
        registry: RendererRegistry,
        pool: SessionPool,
        release_playback: Callable[[str], Awaitable[Any]],
        *,
        hold_s: float = _HOLD_S,
    ):
        self._registry = registry
        self._pool = pool
        self._release_playback = release_playback
        self._hold_s = hold_s
        self._session: Optional[PlaybackSession] = None
        self._release_task: Optional[asyncio.Task] = None

    async def play(self, renderer_id: str, channel: str, source_url: str) -> None:
        """Sound one channel on this renderer, stopping playback if it holds it.

        `source_url` is absolute: the caller resolves it, so it names an address
        this server is known to answer on.

        Raises what claiming a renderer raises: RendererUnavailable when it is
        not connected, RendererBusy when another Core has it, SessionOpenFailed
        or asyncio.TimeoutError when it does not answer.
        """
        await self._release_playback(renderer_id)
        session = await self._session_for(renderer_id)
        await session.set_source(source_url, mime_type=TONE_MIME_TYPE)
        self._arm_release()
        logger.info("Test tone (%s) on renderer %s", channel, renderer_id)

    async def shutdown(self) -> None:
        self._cancel_release()
        await self._close()

    async def _session_for(self, renderer_id: str) -> PlaybackSession:
        session = self._session
        if (
            session is not None
            and session.renderer_id == renderer_id
            and session.state is SessionState.ACTIVE
        ):
            return session
        # A tone still sounding on a different renderer, or a session that has
        # since been closed under us.
        await self._close()
        session = await self._pool.open(renderer_id, announce=False)
        session.on_state(self._on_state)
        session.on_closed(self._on_closed)
        self._session = session
        return session

    async def _close(self) -> None:
        session, self._session = self._session, None
        if session is not None and session.state is not SessionState.CLOSED:
            await session.close(CloseReason.CLOSED_BY_SERVER)

    def _on_state(
        self, session: PlaybackSession, change: StateChange, snapshot: dict
    ) -> None:
        if session is not self._session or change is not StateChange.PLAYBACK:
            return
        state = from_snapshot(snapshot)
        if state is not None and state.state in _DONE_STATES:
            self._cancel_release()
            detach(self._close())

    def _on_closed(self, session: PlaybackSession, reason: CloseReason) -> None:
        if session is self._session:
            self._session = None
            self._cancel_release()

    def _arm_release(self) -> None:
        self._cancel_release()
        self._release_task = asyncio.get_running_loop().create_task(
            self._release_after(self._hold_s)
        )

    async def _release_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._release_task = None
        await self._close()

    def _cancel_release(self) -> None:
        if self._release_task is not None:
            self._release_task.cancel()
            self._release_task = None

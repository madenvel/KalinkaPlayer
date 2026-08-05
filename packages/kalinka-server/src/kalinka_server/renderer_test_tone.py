"""The speaker test: a short tone played through a renderer.

The renderer generates the tone itself — `tone://<channel>?freq&duration_ms` is
a source it plays like any other, with no decoder attached — so this is only
about who holds the renderer while it sounds.

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
# Matches the two-second left/right segments the client plays.
TONE_DURATION_MS = 2000
CHANNELS = ("left", "right", "both")

# Backstop for a renderer that never reports the end of the tone; the session
# is normally closed by the FINISHED that follows it.
_HOLD_S = TONE_DURATION_MS / 1000 + 2.0

_DONE_STATES = (
    AudioGraphNodeState.FINISHED,
    AudioGraphNodeState.STOPPED,
    AudioGraphNodeState.ERROR,
)


def tone_uri(channel: str) -> str:
    return (
        f"tone://{channel}?freq={TONE_FREQUENCY_HZ}"
        f"&duration_ms={TONE_DURATION_MS}"
    )


class TonePlayer:
    """Plays test tones on renderers, one session at a time."""

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
        # Asks whoever is playing to give the renderer up. Returns once it has.
        self._release_playback = release_playback
        self._hold_s = hold_s
        self._session: Optional[PlaybackSession] = None
        self._release_task: Optional[asyncio.Task] = None

    async def play(self, renderer_id: str, channel: str) -> None:
        """Sound one channel on this renderer, stopping playback if it holds it.

        Raises what claiming a renderer raises: RendererUnavailable when it is
        not connected, RendererBusy when another Core has it, SessionOpenFailed
        or asyncio.TimeoutError when it does not answer.
        """
        if channel not in CHANNELS:
            channel = "both"
        await self._release_playback(renderer_id)
        session = await self._session_for(renderer_id)
        await session.set_source(tone_uri(channel))
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

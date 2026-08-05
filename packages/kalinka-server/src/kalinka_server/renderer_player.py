"""The play queue's player, backed by a session on the active renderer.

Presents the surface the embedded AudioPlayer had — append / remove /
clear_all / pause / resume / stop / seek / get_state, plus monitor() — so the
play queue does not know playback moved out of process. Commands stay
fire-and-forget: they return at once and their effect comes back as state,
delivered through the monitor in the same StreamState shape the native player
produced.

The session is opened on demand: the first append() claims the registry's
active renderer (the client-selected one when connected, otherwise the first
connected). It is released when playback stops — stop() closes it
immediately, a finished or errored queue releases it after a short grace, and a
paused one after a longer timeout. A session the renderer side ends
(restart, another Core, renderer lost) surfaces as a STOPPED state with a
message; the next play opens a fresh one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .config_model import KalinkaConfig
from .renderer_registry import RendererRegistry, RendererUnavailable
from .renderer_sessions import (
    CloseReason,
    PlaybackSession,
    RendererBusy,
    SessionNotActive,
    SessionOpenFailed,
    SessionPool,
    SessionState,
)
from .renderer_state import StateChange
from .stream_state import (
    AudioGraphNodeState,
    StateMonitor,
    StreamError,
    StreamErrorSource,
    StreamState,
    from_snapshot,
)

logger = logging.getLogger(__name__.split(".")[-1])

# How long a finished / stopped / errored session is held before release, so an
# auto-advance that is still resolving the next URL does not reopen every time.
IDLE_RELEASE_TIMEOUT_S = 15.0

# How long a paused session is held before release; a config setting later.
PAUSE_RELEASE_TIMEOUT_S = 600.0


# Reasons the play queue need not hear about: the first is our own close, the
# second is the whole server going down.
_QUIET_CLOSE_REASONS = {CloseReason.CLOSED_BY_SERVER, CloseReason.SHUTDOWN}


class RendererPlayer:
    def __init__(
        self,
        config: KalinkaConfig,
        registry: RendererRegistry,
        sessions: SessionPool,
        monitor: StateMonitor,
    ):
        self._config = config
        self._registry = registry
        self._pool = sessions
        # Passed in, not owned: the queue listens to one monitor for its whole
        # life, while a player lasts only as long as the renderer it drives.
        self._monitor = monitor
        self._last_state = StreamState(
            state=AudioGraphNodeState.STOPPED, timestamp=time.monotonic_ns()
        )
        self._session: Optional[PlaybackSession] = None
        # Commands are applied strictly in call order by one sender task.
        self._ops: asyncio.Queue = asyncio.Queue()
        self._sender_task: Optional[asyncio.Task] = None
        self._release_task: Optional[asyncio.Task] = None
        # Bumped on every published state; an armed release fires only if the
        # player is still in the state that armed it.
        self._epoch = 0

    # ------------------------------------------------------------------
    # The AudioPlayer surface

    def get_state(self) -> StreamState:
        return self._last_state

    def append(self, stream_id: int, url: str, mime_type: str) -> None:
        self._submit("append", stream_id, url, mime_type)

    def remove(self, stream_id: int) -> None:
        self._submit("remove", stream_id)

    def clear_all(self) -> None:
        self._submit("clear_all")

    def pause(self) -> None:
        self._submit("pause")

    def resume(self) -> None:
        self._submit("resume")

    def stop(self) -> None:
        self._submit("stop")

    def seek(self, position_ms: int) -> int:
        self._submit("seek", position_ms)
        # Echo the target; the reached position comes back as state.
        return position_ms

    async def shutdown(self) -> None:
        """Drop the session and stop the sender; the monitor is the caller's."""
        for task in (self._release_task, self._sender_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._release_task = None
        self._sender_task = None
        session, self._session = self._session, None
        if session is not None and session.state is not SessionState.CLOSED:
            await session.close(CloseReason.CLOSED_BY_SERVER)

    # ------------------------------------------------------------------
    # Command delivery

    def _submit(self, op: str, *args) -> None:
        if self._sender_task is None or self._sender_task.done():
            self._sender_task = asyncio.get_running_loop().create_task(self._run())
        self._ops.put_nowait((op, args))

    async def _run(self) -> None:
        while True:
            op, args = await self._ops.get()
            try:
                await self._dispatch(op, *args)
            except (
                RendererUnavailable,
                RendererBusy,
                SessionOpenFailed,
                SessionNotActive,
                asyncio.TimeoutError,
            ) as exc:
                logger.warning("Renderer command %s failed: %s", op, exc)
                self._publish(
                    StreamState(
                        state=AudioGraphNodeState.ERROR,
                        timestamp=time.monotonic_ns(),
                        error=StreamError(
                            source=StreamErrorSource.AUDIO_OUTPUT,
                            message=str(exc) or "renderer unavailable",
                        ),
                    )
                )
            except Exception:
                logger.exception("Renderer command %s failed", op)

    async def _dispatch(self, op: str, *args) -> None:
        if op == "append":
            stream_id, url, mime_type = args
            session = await self._ensure_session()
            await session.enqueue_source(
                url, mime_type=mime_type or "", source_token=str(stream_id)
            )
            return
        if op == "stop":
            await self._release(synthesize_stopped=True)
            return

        session = self._session
        if session is None:
            logger.debug("Dropping %s: no renderer session", op)
            return
        if op == "remove":
            await session.remove_source(str(args[0]))
        elif op == "clear_all":
            await session.clear_queue()
        elif op == "pause":
            await session.pause()
        elif op == "resume":
            await session.resume()
        elif op == "seek":
            await session.seek(args[0])

    # ------------------------------------------------------------------
    # Session lifecycle

    async def _ensure_session(self) -> PlaybackSession:
        if self._session is not None and self._session.state is not SessionState.CLOSED:
            return self._session
        renderer_id = self._registry.active_id()
        if renderer_id is None:
            raise RendererUnavailable("no renderer is connected")
        session = await self._pool.open(renderer_id)
        session.on_state(self._on_session_state)
        session.on_closed(self._on_session_closed)
        self._session = session
        logger.info("Claimed renderer %s for playback", renderer_id)
        return session

    async def release_unless_on(self, renderer_id: Optional[str]) -> None:
        """Drop a session held on any renderer but this one, stopping playback
        there. Called before a selection change lands, so the STOPPED belongs
        to the renderer that was playing rather than to its replacement."""
        session = self._session
        if session is None or session.renderer_id == renderer_id:
            return
        await self._release(synthesize_stopped=True)

    async def _release(self, synthesize_stopped: bool) -> None:
        self._cancel_release()
        session, self._session = self._session, None
        if session is None:
            return
        await session.close(CloseReason.CLOSED_BY_SERVER)
        if synthesize_stopped:
            self._publish(
                StreamState(
                    state=AudioGraphNodeState.STOPPED, timestamp=time.monotonic_ns()
                )
            )

    def _on_session_closed(self, session: PlaybackSession, reason: CloseReason) -> None:
        if session is not self._session:
            return  # a close we initiated; already detached
        self._session = None
        self._cancel_release()
        if reason in _QUIET_CLOSE_REASONS:
            return
        logger.warning("Renderer session ended: %s", reason.value)
        self._publish(
            StreamState(
                state=AudioGraphNodeState.STOPPED,
                timestamp=time.monotonic_ns(),
                error=StreamError(
                    source=StreamErrorSource.AUDIO_OUTPUT,
                    message=f"renderer session ended ({reason.value})",
                ),
            )
        )

    # ------------------------------------------------------------------
    # State delivery

    def _on_session_state(
        self, session: PlaybackSession, change: StateChange, snapshot: dict
    ) -> None:
        if session is not self._session:
            return
        if change is StateChange.SOURCE:
            self._publish(
                StreamState(
                    state=AudioGraphNodeState.SOURCE_CHANGED,
                    timestamp=time.monotonic_ns(),
                )
            )
            return
        translated = from_snapshot(snapshot)
        if translated is None:
            return
        if change is StateChange.SNAPSHOT:
            # The baseline at open (and on resume): track it, don't act on it.
            self._last_state = translated
            return
        if change is StateChange.PLAYBACK:
            self._publish(translated)

    def _publish(self, state: StreamState) -> None:
        self._last_state = state
        self._epoch += 1
        self._arm_release(state)
        self._monitor.push(state)

    # ------------------------------------------------------------------
    # Idle release

    def _arm_release(self, state: StreamState) -> None:
        self._cancel_release()
        if self._session is None:
            return
        if state.state is AudioGraphNodeState.PAUSED:
            self._release_task = asyncio.get_running_loop().create_task(
                self._release_after(
                    PAUSE_RELEASE_TIMEOUT_S, self._epoch, synthesize=True
                )
            )
        elif state.state in (
            AudioGraphNodeState.FINISHED,
            AudioGraphNodeState.STOPPED,
            AudioGraphNodeState.ERROR,
        ):
            self._release_task = asyncio.get_running_loop().create_task(
                self._release_after(IDLE_RELEASE_TIMEOUT_S, self._epoch, synthesize=False)
            )

    async def _release_after(self, delay: float, epoch: int, synthesize: bool) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if epoch != self._epoch or self._session is None:
            return
        # This task is done waiting; _release must not cancel it mid-close.
        self._release_task = None
        logger.info("Releasing idle renderer session")
        await self._release(synthesize_stopped=synthesize)

    def _cancel_release(self) -> None:
        if self._release_task is not None:
            self._release_task.cancel()
            self._release_task = None

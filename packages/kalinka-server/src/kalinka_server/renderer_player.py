"""The play queue's player, backed by a session on the active renderer.

Presents the surface the embedded AudioPlayer had — append / remove /
clear_all / pause / resume / stop / seek / get_state, plus monitor() — so the
play queue does not know playback moved out of process. Commands stay
fire-and-forget: they return at once and their effect comes back as state,
delivered through the monitor in the same StreamState shape the native player
produced.

The session is opened on demand: the first append() claims the first connected
renderer in the registry. It is released when playback stops — stop() closes it
immediately, a finished or errored queue releases it after a short grace, and a
paused one after a longer timeout. A session the renderer side ends
(restart, another Core, renderer lost) surfaces as a STOPPED state with a
message; the next play opens a fresh one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .config_model import KalinkaConfig
from .renderer_registry import RendererRegistry
from .renderer_sessions import (
    CloseReason,
    PlaybackSession,
    RendererBusy,
    RendererUnavailable,
    SessionNotActive,
    SessionOpenFailed,
    SessionPool,
    SessionState,
)

logger = logging.getLogger(__name__.split(".")[-1])

# How long a finished / stopped / errored session is held before release, so an
# auto-advance that is still resolving the next URL does not reopen every time.
IDLE_RELEASE_TIMEOUT_S = 15.0

# How long a paused session is held before release; a config setting later.
PAUSE_RELEASE_TIMEOUT_S = 600.0


class AudioGraphNodeState(Enum):
    """Playback states, mirroring the native player's enum of the same name."""

    ERROR = -1
    STOPPED = 0
    PREPARING = 1
    STREAMING = 2
    PAUSED = 3
    FINISHED = 4
    SOURCE_CHANGED = 5


class StreamErrorSource(Enum):
    NONE = 0
    HTTP_STREAM = 1
    AUDIO_OUTPUT = 2
    DECODER = 3


class StreamType(Enum):
    BYTES = 0
    FRAMES = 1


@dataclass
class AudioFormatInfo:
    sample_rate: int = 0
    channels: int = 0
    bits_per_sample: int = 0


@dataclass
class StreamInfo:
    format: AudioFormatInfo = field(default_factory=AudioFormatInfo)
    stream_type: StreamType = StreamType.FRAMES
    stream_size: int = 0


@dataclass
class StreamError:
    source: StreamErrorSource = StreamErrorSource.NONE
    message: str = ""


@dataclass
class StreamState:
    state: AudioGraphNodeState
    # Position when the state was reported; timestamp is the local receipt
    # time (monotonic ns), so extrapolation needs no cross-machine clock.
    position: int = 0
    timestamp: int = 0
    error: Optional[StreamError] = None
    stream_info: Optional[StreamInfo] = None


_STATE_NAMES = {
    "stopped": AudioGraphNodeState.STOPPED,
    "preparing": AudioGraphNodeState.PREPARING,
    "playing": AudioGraphNodeState.STREAMING,
    "paused": AudioGraphNodeState.PAUSED,
    "finished": AudioGraphNodeState.FINISHED,
    "error": AudioGraphNodeState.ERROR,
}

_ERROR_SOURCES = {
    "none": StreamErrorSource.NONE,
    "http_stream": StreamErrorSource.HTTP_STREAM,
    "audio_output": StreamErrorSource.AUDIO_OUTPUT,
    "decoder": StreamErrorSource.DECODER,
}

# Reasons the play queue need not hear about: the first is our own close, the
# second is the whole server going down.
_QUIET_CLOSE_REASONS = {CloseReason.CLOSED_BY_SERVER, CloseReason.SHUTDOWN}


def _to_stream_info(fmt: Optional[dict]) -> Optional[StreamInfo]:
    if not fmt:
        return None
    return StreamInfo(
        format=AudioFormatInfo(
            sample_rate=fmt.get("sample_rate_hz", 0),
            channels=fmt.get("channels", 0),
            bits_per_sample=fmt.get("bits_per_sample", 0),
        ),
        stream_type=(
            StreamType.FRAMES
            if fmt.get("stream_kind") == "frames"
            else StreamType.BYTES
        ),
        stream_size=fmt.get("stream_size_units", 0),
    )


def _to_error(error: Optional[dict]) -> Optional[StreamError]:
    if not error:
        return None
    return StreamError(
        source=_ERROR_SOURCES.get(error.get("source") or "", StreamErrorSource.NONE),
        message=error.get("message") or "",
    )


class StateMonitor:
    """Async-iterable stream of StreamState, in place of the native monitor."""

    _STOP = object()

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = True

    def push(self, state: StreamState) -> None:
        if self._running:
            self._queue.put_nowait(state)

    def stop(self) -> None:
        self._running = False
        self._queue.put_nowait(self._STOP)

    def is_running(self) -> bool:
        return self._running

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamState:
        if not self._running:
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is self._STOP:
            raise StopAsyncIteration
        return item


class RendererPlayer:
    def __init__(
        self,
        config: KalinkaConfig,
        registry: RendererRegistry,
        sessions: SessionPool,
    ):
        self._config = config
        self._registry = registry
        self._pool = sessions
        self._monitor = StateMonitor()
        self._last_state = StreamState(
            state=AudioGraphNodeState.STOPPED, timestamp=time.monotonic_ns()
        )
        # The native monitor reported the current state on subscription; the
        # play queue's initial STOPPED event to clients relies on it.
        self._monitor.push(self._last_state)
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

    def monitor(self) -> StateMonitor:
        return self._monitor

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
        renderer_id = self._registry.first_connected_id()
        if renderer_id is None:
            raise RendererUnavailable("no renderer is connected")
        session = await self._pool.open(renderer_id)
        session.on_state(self._on_session_state)
        session.on_closed(self._on_session_closed)
        self._session = session
        logger.info("Claimed renderer %s for playback", renderer_id)
        return session

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
        self, session: PlaybackSession, payload: str, snapshot: dict
    ) -> None:
        if session is not self._session:
            return
        if payload == "source_changed":
            self._publish(
                StreamState(
                    state=AudioGraphNodeState.SOURCE_CHANGED,
                    timestamp=time.monotonic_ns(),
                )
            )
            return
        state = _STATE_NAMES.get(snapshot.get("playback_state") or "")
        if state is None:
            return
        translated = StreamState(
            state=state,
            position=snapshot.get("position_ms", 0),
            timestamp=time.monotonic_ns(),
            error=_to_error(snapshot.get("error")),
            stream_info=_to_stream_info(snapshot.get("format")),
        )
        if payload == "state_snapshot":
            # The baseline at open (and on resume): track it, don't act on it.
            self._last_state = translated
            return
        if payload == "playback_state_changed":
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

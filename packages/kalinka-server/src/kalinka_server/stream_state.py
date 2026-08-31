"""What the play queue calls playback state, and how a renderer snapshot maps
onto it.

The vocabulary is the embedded AudioPlayer's, kept unchanged when playback
moved out of process: the queue reasons in these terms and does not know a
renderer is involved. It lives here rather than with the renderer player so
that the queue does not have to import the renderer to name its own states.

:func:`from_snapshot` is the whole translation from the wire's merged snapshot
dict (see :mod:`renderer_state`) into a ``StreamState`` — a pure function, so
it is testable without a session or a player.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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


@dataclass
class AudioFormatInfo:
    sample_rate: int = 0
    channels: int = 0
    bits_per_sample: int = 0


@dataclass
class StreamInfo:
    format: AudioFormatInfo = field(default_factory=AudioFormatInfo)
    # None, never 0, when the renderer cannot tell: 0 reads as "already ended".
    duration_ms: Optional[int] = None


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
    # Which appended stream this is about, None when the renderer named none.
    stream_id: Optional[int] = None

    def position_at(self, now_ns: int) -> int:
        """Where playback has reached: only a running stream advances."""
        if self.state is not AudioGraphNodeState.STREAMING:
            return self.position
        return self.position + max(0, (now_ns - self.timestamp) // 1_000_000)


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


def to_stream_info(fmt: Optional[dict]) -> Optional[StreamInfo]:
    if not fmt:
        return None
    return StreamInfo(
        format=AudioFormatInfo(
            sample_rate=fmt.get("sample_rate_hz", 0),
            channels=fmt.get("channels", 0),
            bits_per_sample=fmt.get("bits_per_sample", 0),
        ),
        duration_ms=fmt.get("duration_ms"),
    )


def to_error(error: Optional[dict]) -> Optional[StreamError]:
    if not error:
        return None
    return StreamError(
        source=_ERROR_SOURCES.get(error.get("source") or "", StreamErrorSource.NONE),
        message=error.get("message") or "",
    )


def to_stream_id(source_token: Optional[str]) -> Optional[int]:
    """The stream id a source token names — the inverse of what the renderer
    player stringifies on the way out. Anything else came from elsewhere."""
    try:
        return int(source_token)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def from_snapshot(snapshot: dict) -> Optional[StreamState]:
    """The queue's view of a renderer snapshot, or None when the renderer has
    not reported a state we play by (``unspecified``, before anything ran)."""
    state = _STATE_NAMES.get(snapshot.get("playback_state") or "")
    if state is None:
        return None
    return StreamState(
        state=state,
        position=snapshot.get("position_ms", 0),
        timestamp=time.monotonic_ns(),
        error=to_error(snapshot.get("error")),
        stream_info=to_stream_info(snapshot.get("format")),
        stream_id=to_stream_id(snapshot.get("source_token")),
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

# kalinka_plugin_sdk/api.py
from collections.abc import Awaitable, Callable, Iterable
from enum import Enum
from typing import Any, Generic, Literal, Optional, Protocol, TypeVar, Union

from pydantic import BaseModel, ConfigDict

from .datamodel import EntityId, PlaybackMode, PlaybackState, Track, TrackList
from .inputmodule import TrackInfo

API_VERSION = "1.0"


class PlayQueueController(Protocol):
    """
    Contract for play queue operations.
    All methods should be implemented by the play queue provider.
    """

    async def play(self, index: Optional[int] = None) -> None:
        """Start playback at the given index, or resume if index is None."""
        ...

    async def play_next(self, index: int) -> None:
        """Play the track at the given index next."""
        ...

    async def pause(self, paused: bool) -> None:
        """Pause or resume playback.
        If paused is True, pause playback; if False, resume playback.
        """
        ...

    async def next(self) -> None:
        """Skip to the next track."""
        ...

    async def prev(self) -> None:
        """Go back to the previous track."""
        ...

    async def seek(self, position_ms: int) -> None:
        """Seek to the given position in milliseconds."""
        ...

    async def stop(self) -> None:
        """Stop playback."""
        ...

    async def add(self, tracks: list[TrackInfo]) -> None:
        """Add tracks to the queue."""
        ...

    async def remove(self, tracks: list[int]) -> None:
        """Remove tracks by their indices."""
        ...

    async def list(self, offset: int, limit: int) -> TrackList:
        """List tracks in the queue with pagination."""
        ...

    async def get_track_info(self, index: int) -> Optional[Track]:
        """Get info for the track at the given index."""
        ...

    async def get_playback_state(self) -> PlaybackState:
        """Get the current playback state."""
        ...

    async def restore_from_state(
        self,
        state: Any,
        track_info_retriever: Callable[[EntityId], Awaitable[TrackInfo]],
    ) -> None:
        """Restore the playback state from a saved state."""
        ...

    async def clear(self):
        """Clear the play queue.
        Stops the playback if it is active.
        """
        ...

    async def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ) -> PlaybackMode:
        """Set playback modes: shuffle, repeat single, repeat all."""
        ...

    async def get_playback_mode(self) -> PlaybackMode:
        """Get the current playback mode."""
        ...


E_contra = TypeVar("E_contra", bound=Enum, contravariant=True)
E = TypeVar("E", bound=Enum)
S = TypeVar("S")


class BaseEvent(BaseModel, Generic[E]):
    """Base event type for user-defined events.

    Users should subclass this and add event-specific fields.
    `event_type` should be set to an enum value describing the event.
    Global sequence `seq` is assigned by the bus on dispatch.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    event_type: E
    seq: int = 0


class ReplayEvent(BaseModel, Generic[S]):
    """Synthetic event delivered first on subscription containing a snapshot of state.

    `seq` is the per-subscription sequence number (starts at 0).
    `state_type` identifies the type of state for proper deserialization.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    event_type: Literal["replay_event"] = "replay_event"
    state_type: str
    state: S
    server_time_ns: int
    seq: int


EV_emit = TypeVar("EV_emit", bound=BaseEvent, contravariant=True)
EV_listen = TypeVar("EV_listen", bound=BaseEvent, covariant=True)


class EventEmitter(Protocol[EV_emit]):
    def dispatch(self, event: EV_emit) -> None:
        """Dispatch a new event to the bus (thread-safe)."""
        ...


class AsyncEventStream(Protocol[EV_listen, S]):
    """Protocol for async iteration over events.

    Represents an async iterable stream of events that can be consumed
    using async for loops. May include replay events at the start of the stream.
    """

    def __aiter__(self) -> "AsyncEventStream[EV_listen, S]":
        """Return the async iterator object."""
        ...

    async def __anext__(self) -> Union[EV_listen, ReplayEvent[S]]:
        """Return the next event in the stream.

        Raises StopAsyncIteration when the stream is closed.
        """
        ...


class EventListener(Protocol[E_contra, EV_listen, S]):
    def subscribe(
        self,
        event_types: Iterable[E_contra],
        callback: Optional[Callable[[Union[EV_listen, ReplayEvent[S]]], None]] = None,
        listener_id: Optional[str] = None,
    ) -> "Subscription":
        """Subscribe to event types with optional callback; returns a Subscription."""
        ...

    def unsubscribe(self, listener_id: str) -> None:
        """Unsubscribe and close the listener's queue."""
        ...

    def stream(
        self,
        event_types: Iterable[E_contra],
    ) -> "AsyncEventStream[EV_listen, S]":
        """Create an async event stream for the given event types.

        Returns an AsyncEventStream that can be consumed using async for loops.
        The stream will include a replay event at the start containing the current state.
        """
        ...


class Subscription(Protocol):
    @property
    def id(self) -> str:  # unique per subscription
        ...

    def unsubscribe(self) -> None: ...


class LoggerAPI(Protocol):
    def debug(self, msg, *args, **kwargs): ...
    def info(self, msg, *args, **kwargs): ...
    def warning(self, msg, *args, **kwargs): ...
    def error(self, msg, *args, **kwargs): ...
    def critical(self, msg, *args, **kwargs): ...
    def fatal(self, msg, *args, **kwargs): ...

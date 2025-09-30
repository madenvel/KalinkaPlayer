# kalinka_plugin_sdk/api.py
from collections.abc import Callable
from typing import Protocol, Optional

from kalinka_plugin_sdk.datamodel import PlaybackMode, PlayerState, Track, TrackList

from .inputmodule import TrackInfo
from .events import EventType

API_VERSION = "1.0"


class PlayQueueAPI(Protocol):
    """
    Contract for play queue operations.
    All methods should be implemented by the play queue provider.
    """

    def play(self, index: Optional[int] = None) -> None:
        """Start playback at the given index, or resume if index is None."""
        ...

    def play_next(self, index: int) -> None:
        """Play the track at the given index next."""
        ...

    def pause(self, paused: bool) -> None:
        """Pause or resume playback.
        If paused is True, pause playback; if False, resume playback.
        """
        ...

    def next(self) -> None:
        """Skip to the next track."""
        ...

    def prev(self) -> None:
        """Go back to the previous track."""
        ...

    def seek(self, position_ms: int) -> None:
        """Seek to the given position in milliseconds."""
        ...

    def stop(self) -> None:
        """Stop playback."""
        ...

    def add(self, tracks: list[TrackInfo]) -> None:
        """Add tracks to the queue."""
        ...

    def remove(self, tracks: list[int]) -> None:
        """Remove tracks by their indices."""
        ...

    def list(self, offset: int, limit: int) -> TrackList:
        """List tracks in the queue with pagination."""
        ...

    def get_track_info(self, index: int) -> Optional[Track]:
        """Get info for the track at the given index."""
        ...

    def get_state(self) -> PlayerState:
        """Get the current playback state."""
        ...

    def clear(self) -> None:
        """Clear the play queue.
        Stops the playback if it is active.
        """
        ...

    def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ) -> None:
        """Set playback modes: shuffle, repeat single, repeat all."""
        ...

    def get_playback_mode(self) -> PlaybackMode:
        """Get the current playback mode."""
        ...


class EventEmitterAPI(Protocol):
    def dispatch(self, topic: EventType, *args, **kwargs) -> None: ...


class SubscriptionHandle(Protocol):
    def unsubscribe(self) -> None: ...


class EventListenerAPI(Protocol):
    def subscribe(self, topic: EventType, handler: Callable) -> SubscriptionHandle: ...


class LoggerAPI(Protocol):
    def debug(self, msg, *args, **kwargs): ...
    def info(self, msg, *args, **kwargs): ...
    def warning(self, msg, *args, **kwargs): ...
    def error(self, msg, *args, **kwargs): ...
    def critical(self, msg, *args, **kwargs): ...
    def fatal(self, msg, *args, **kwargs): ...


class PluginContext:
    playqueue: PlayQueueAPI
    event_emitter: EventEmitterAPI
    listener: EventListenerAPI
    logger: LoggerAPI
    plugin_id: str
    sdk_version: str  # equals API_VERSION
    capabilities: set[str]

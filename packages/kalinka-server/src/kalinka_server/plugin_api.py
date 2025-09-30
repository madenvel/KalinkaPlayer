from typing import Optional
from kalinka_plugin_sdk.api import (
    PlayQueueAPI,
    EventEmitterAPI,
)
from kalinka_plugin_sdk.datamodel import PlaybackMode, PlayerState, Track, TrackList
from kalinka_plugin_sdk.events import EventType
from kalinka_plugin_sdk.inputmodule import TrackInfo

from .async_common import EventEmitter
from .playqueue import PlayQueue


class PlayQueueAPIImpl(PlayQueueAPI):
    """
    Contract for play queue operations.
    All methods should be implemented by the play queue provider.
    """

    def __init__(self, playqueue: PlayQueue):
        self.__playqueue = playqueue

    def play(self, index: Optional[int] = None) -> None:
        """Start playback at the given index, or resume if index is None."""
        self.__playqueue.play(index)

    def play_next(self, index: int) -> None:
        """Play the track at the given index next."""
        self.__playqueue.play_next(index)

    def pause(self, paused: bool) -> None:
        """Pause or resume playback.
        If paused is True, pause playback; if False, resume playback.
        """
        self.__playqueue.pause(paused)

    def next(self) -> None:
        """Skip to the next track."""
        self.__playqueue.next()

    def prev(self) -> None:
        """Go back to the previous track."""
        self.__playqueue.prev()

    def seek(self, position_ms: int) -> None:
        """Seek to the given position in milliseconds."""
        self.__playqueue.seek(position_ms)

    def stop(self) -> None:
        """Stop playback."""
        self.__playqueue.stop()

    def add(self, tracks: list[TrackInfo]) -> None:
        """Add tracks to the queue."""
        self.__playqueue.add(tracks)

    def remove(self, tracks: list[int]) -> None:
        """Remove tracks by their indices."""
        self.__playqueue.remove(tracks)

    def list(self, offset: int, limit: int) -> TrackList:
        """List tracks in the queue with pagination."""
        return self.__playqueue.list(offset, limit)

    def get_track_info(self, index: int) -> Optional[Track]:
        """Get info for the track at the given index."""
        return self.__playqueue.get_track_info(index)

    def get_state(self) -> PlayerState:
        """Get the current playback state."""
        return self.__playqueue.get_state()

    def clear(self) -> None:
        """Clear the play queue.
        Stops the playback if it is active.
        """
        self.__playqueue.clear()

    def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ) -> None:
        """Set playback modes: shuffle, repeat single, repeat all."""
        self.__playqueue.set_playback_mode(shuffle, repeat_single, repeat_all)

    def get_playback_mode(self) -> PlaybackMode:
        """Get the current playback mode."""
        return self.__playqueue.get_playback_mode()


class EventEmitterAPIImpl(EventEmitterAPI):
    def __init__(self, event_emitter: EventEmitter):
        self.__event_emitter = event_emitter

    def dispatch(self, topic: EventType, *args, **kwargs) -> None:
        self.__event_emitter.dispatch(topic, *args, **kwargs)

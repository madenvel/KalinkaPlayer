from enum import Enum
from typing import List

from pydantic import BaseModel

from .api import BaseEvent

from .datamodel import (
    Track,
    PlaybackState,
    PlaybackMode,
)


class PlayQueueEventType(Enum):
    PlaybackStateChanged = "state_changed"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "tracks_added"
    TracksRemoved = "tracks_removed"
    PlaybackError = "playback_error"
    PlaybackModeChanged = "playback_mode_changed"


class PlayQueueEvent(BaseEvent[PlayQueueEventType]):
    """Base class for all play queue events."""

    pass


class PlayQueueState(BaseModel):
    """State model for playqueue - uses Pydantic BaseModel for serialization."""

    playback_state: PlaybackState
    track_list: List[Track]
    playback_mode: PlaybackMode
    seq: int = 0

    def apply(self, event: PlayQueueEvent) -> None:
        """Apply event to create a new state (mutable pattern)."""
        if self.seq >= event.seq:
            return

        if isinstance(event, PlaybackStateChangedEvent):
            self.playback_state = event.state
        elif isinstance(event, TracksAddedEvent):
            self.track_list = self.track_list + event.tracks
        elif isinstance(event, TracksRemovedEvent):
            new_track_list = [
                track
                for i, track in enumerate(self.track_list)
                if i not in event.indices
            ]
            self.track_list = new_track_list
        elif isinstance(event, PlaybackModeChangedEvent):
            self.playback_mode = event.mode

        self.seq = event.seq


class PlaybackStateChangedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackStateChanged
    state: PlaybackState


class RequestMoreTracksEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.RequestMoreTracks


class TracksAddedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TracksAdded
    tracks: List[Track]


class TracksRemovedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TracksRemoved
    indices: List[int]


class PlaybackModeChangedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackModeChanged
    mode: PlaybackMode


class PlaybackErrorEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackError
    message: str

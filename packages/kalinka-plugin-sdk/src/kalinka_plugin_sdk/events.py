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
    playbackState: PlaybackState
    trackList: List[Track]
    playbackMode: PlaybackMode

    def apply(self, event: PlayQueueEvent) -> None:
        if isinstance(event, PlaybackStateChangedEvent):
            self.playbackState = event.state
        elif isinstance(event, TracksAddedEvent):
            self.trackList.extend(event.tracks)
        elif isinstance(event, TracksRemovedEvent):
            self.trackList = [
                track
                for i, track in enumerate(self.trackList)
                if i not in event.indices
            ]
        elif isinstance(event, PlaybackModeChangedEvent):
            self.playbackMode = event.mode


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

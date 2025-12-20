from enum import Enum
from typing import List, Union
from abc import ABC, abstractmethod

from pydantic import BaseModel

from .datamodel import EntityId, Track, PlayerState, PlaybackMode, TrackList


class EventType(Enum):
    StateChanged = "state_changed"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "tracks_added"
    TracksRemoved = "tracks_removed"
    NetworkError = "network_error"
    FavoriteAdded = "favorite_added"
    FavoriteRemoved = "favorite_removed"
    VolumeChanged = "volume_changed"
    StateReplay = "state_replay"
    PlaybackModeChanged = "playback_mode_changed"


class BaseEventPayload(BaseModel, ABC):
    # Sequence number assigned by the dispatcher to preserve ordering across threads.
    sequence: int = 0

    @property
    @abstractmethod
    def event_type(self) -> EventType: ...


class FavoriteAddedEvent(BaseEventPayload):
    id: EntityId

    @property
    def event_type(self) -> EventType:
        return EventType.FavoriteAdded


class FavoriteRemovedEvent(BaseEventPayload):
    id: EntityId

    @property
    def event_type(self) -> EventType:
        return EventType.FavoriteRemoved


class VolumeChangedEvent(BaseEventPayload):
    volume: int

    @property
    def event_type(self) -> EventType:
        return EventType.VolumeChanged


class TracksAddedEvent(BaseEventPayload):
    tracks: List[Track]

    @property
    def event_type(self) -> EventType:
        return EventType.TracksAdded


class TracksRemovedEvent(BaseEventPayload):
    indices: List[int]

    @property
    def event_type(self) -> EventType:
        return EventType.TracksRemoved


class StateChangedEvent(BaseEventPayload):
    state: PlayerState

    @property
    def event_type(self) -> EventType:
        return EventType.StateChanged


class StateReplayEvent(BaseEventPayload):
    state: PlayerState
    track_list: TrackList
    playback_mode: PlaybackMode

    @property
    def event_type(self) -> EventType:
        return EventType.StateReplay


class PlaybackModeChangedEvent(BaseEventPayload):
    mode: PlaybackMode

    @property
    def event_type(self) -> EventType:
        return EventType.PlaybackModeChanged


class NetworkErrorEvent(BaseEventPayload):
    message: str

    @property
    def event_type(self) -> EventType:
        return EventType.NetworkError


class RequestMoreTracksEvent(BaseEventPayload):
    """Event with no payload data."""

    @property
    def event_type(self) -> EventType:
        return EventType.RequestMoreTracks


# Union type of all possible event payloads
AnyEventPayload = Union[
    FavoriteAddedEvent,
    FavoriteRemovedEvent,
    VolumeChangedEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
    StateChangedEvent,
    StateReplayEvent,
    PlaybackModeChangedEvent,
    NetworkErrorEvent,
    RequestMoreTracksEvent,
]

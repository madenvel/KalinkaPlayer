from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from pydantic import BaseModel

from .datamodel import EntityId


class EventType(Enum):
    StateChanged = "state_changed"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "track_added"
    TracksRemoved = "track_removed"
    NetworkError = "network_error"
    FavoriteAdded = "favorite_added"
    FavoriteRemoved = "favorite_removed"
    VolumeChanged = "volume_changed"
    StateReplay = "state_replay"
    PlaybackModeChanged = "playback_mode_changed"


class FavoriteAddedEvent(BaseModel):
    id: EntityId


class FavoriteRemovedEvent(BaseModel):
    id: EntityId

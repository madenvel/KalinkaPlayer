from enum import Enum


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

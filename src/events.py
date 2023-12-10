from enum import Enum


class EventType(Enum):
    Playing = "playing"
    Paused = "paused"
    Stopped = "stopped"
    Progress = "current_progress"
    Error = "error"
    Bufferring = "buffering"
    TrackChanged = "change_track"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "track_added"
    TracksRemoved = "track_removed"
    NetworkError = "network_error"

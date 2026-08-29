from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .api import BaseEvent, BaseState

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
    TrackMoved = "track_moved"
    TrackUnavailable = "track_unavailable"
    PlaybackError = "playback_error"
    PlaybackModeChanged = "playback_mode_changed"
    RenderersChanged = "renderers_changed"
    CurrentRendererChanged = "current_renderer_changed"


class RendererDescriptor(BaseModel):
    """One playback endpoint as the Core knows it.

    Deliberately carries no active/selected marker: which renderer is current
    is a separate axis of state (``CurrentRendererChangedEvent``), so one
    fact never rides two events.
    """

    renderer_id: str
    instance_id: str = ""
    friendly_name: str = ""
    software_version: str = ""
    kind: str = ""
    status: str = "offline"
    platform: Dict[str, str] = Field(default_factory=dict)
    connected_at: float = 0.0
    last_seen: float = 0.0


class PlayQueueEvent(BaseEvent[PlayQueueEventType]):
    """Base class for all play queue events."""

    pass


class PlayQueueState(BaseState[PlayQueueEvent]):
    """State model for playqueue - uses Pydantic BaseModel for serialization."""

    playback_state: PlaybackState
    track_list: List[Track]
    playback_mode: PlaybackMode
    renderers: List[RendererDescriptor] = Field(default_factory=list)
    current_renderer_id: Optional[str] = None
    selected_renderer_id: Optional[str] = None

    def apply(self, event: PlayQueueEvent) -> "PlayQueueState":
        """Apply event and return a new state (immutable pattern)."""
        if self.seq >= event.seq:
            return self

        updates: dict[str, Any] = {"seq": event.seq}

        if isinstance(event, PlaybackStateChangedEvent):
            updates["playback_state"] = event.state
        elif isinstance(event, TracksAddedEvent):
            track_list = list(self.track_list)
            for i, track in enumerate(event.tracks):
                track_list.insert(event.index + i, track)
            updates["track_list"] = track_list
        elif isinstance(event, TracksRemovedEvent):
            new_track_list = [
                track
                for i, track in enumerate(self.track_list)
                if i not in event.indices
            ]
            updates["track_list"] = new_track_list
        elif isinstance(event, TrackMovedEvent):
            track_list = list(self.track_list)
            track = track_list.pop(event.from_index)
            track_list.insert(event.to_index, track)
            updates["track_list"] = track_list
        elif isinstance(event, TrackUnavailableEvent):
            if not (0 <= event.index < len(self.track_list)):
                return self
            track_list = list(self.track_list)
            track_list[event.index] = track_list[event.index].model_copy(
                update={"unavailable": event.unavailable}
            )
            updates["track_list"] = track_list
        elif isinstance(event, PlaybackModeChangedEvent):
            updates["playback_mode"] = event.mode
        elif isinstance(event, RenderersChangedEvent):
            updates["renderers"] = event.renderers
        elif isinstance(event, CurrentRendererChangedEvent):
            updates["current_renderer_id"] = event.renderer_id
            updates["selected_renderer_id"] = event.selected_renderer_id
        else:
            return self

        return self.model_copy(update=updates)


class PlaybackStateChangedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackStateChanged
    state: PlaybackState


class RequestMoreTracksEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.RequestMoreTracks


class TracksAddedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TracksAdded
    tracks: List[Track]
    index: int  # actual list index at which tracks were inserted


class TracksRemovedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TracksRemoved
    indices: List[int]


class TrackMovedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TrackMoved
    from_index: int
    to_index: int


class PlaybackModeChangedEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackModeChanged
    mode: PlaybackMode


class TrackUnavailableEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.TrackUnavailable
    index: int
    # True marks the track as unavailable (URL retrieval failed); False clears
    # the flag once the track has been streamed successfully again.
    unavailable: bool = True


class PlaybackErrorEvent(PlayQueueEvent):
    event_type: PlayQueueEventType = PlayQueueEventType.PlaybackError
    message: str


class RenderersChangedEvent(PlayQueueEvent):
    """Full snapshot of the known renderers; idempotent by construction, so a
    duplicate or missed event never leaves a client wrong for long."""

    event_type: PlayQueueEventType = PlayQueueEventType.RenderersChanged
    renderers: List[RendererDescriptor]


class CurrentRendererChangedEvent(PlayQueueEvent):
    """Which renderer playback runs on: ``renderer_id`` is the effective one,
    ``selected_renderer_id`` the client's pin (None = automatic)."""

    event_type: PlayQueueEventType = PlayQueueEventType.CurrentRendererChanged
    renderer_id: Optional[str] = None
    selected_renderer_id: Optional[str] = None

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Dict, List

from kalinka_plugin_sdk.datamodel import (
    DeviceVolume,
    PlaybackMode,
    PlayerState,
    Track,
    TrackList,
)
from kalinka_plugin_sdk.events import (
    AnyEventPayload,
    EventType,
    PlaybackModeChangedEvent,
    StateChangedEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
    VolumeChangedEvent,
)

from .async_common import EventListener, Subscription


@dataclass
class State:
    playback_mode: PlaybackMode = field(
        default_factory=lambda: PlaybackMode(
            shuffle=False, repeat_single=False, repeat_all=False
        )
    )
    player_state: PlayerState = field(default_factory=PlayerState)
    track_list: TrackList = field(
        default_factory=lambda: TrackList(offset=0, limit=0, total=0, items=[])
    )
    device_volume: DeviceVolume = field(default_factory=DeviceVolume)
    sequence: int = 0

    def copy(self) -> "State":
        return copy.deepcopy(self)


class StateManager:
    def __init__(self, event_listener: EventListener):
        self._state = State()
        self._lock = RLock()
        self._subscriptions: List[Subscription] = []
        self._subscriptions = self._register_subscriptions(event_listener)

    def _register_subscriptions(
        self, event_listener: EventListener
    ) -> List[Subscription]:
        handlers: Dict[EventType, Callable[[Any], None]] = {
            EventType.StateChanged: self._handle_state_changed,
            EventType.PlaybackModeChanged: self._handle_playback_mode_changed,
            EventType.TracksAdded: self._handle_tracks_added,
            EventType.TracksRemoved: self._handle_tracks_removed,
            EventType.VolumeChanged: self._handle_volume_changed,
        }
        return [event_listener.subscribe(event, cb) for event, cb in handlers.items()]

    def _update_sequence(self, payload: AnyEventPayload) -> None:
        sequence = getattr(payload, "sequence", None) or (self._state.sequence + 1)
        self._state.sequence = max(self._state.sequence, sequence)

    def _handle_state_changed(self, payload: StateChangedEvent) -> None:
        with self._lock:
            self._state.player_state = payload.state.model_copy(deep=True)
            self._update_sequence(payload)

    def _handle_playback_mode_changed(self, payload: PlaybackModeChangedEvent) -> None:
        with self._lock:
            self._state.playback_mode = payload.mode.model_copy(deep=True)
            self._update_sequence(payload)

    def _handle_tracks_added(self, payload: TracksAddedEvent) -> None:
        with self._lock:
            current = self._state.track_list
            items: List[Track] = list(current.items)
            items.extend([track.model_copy(deep=True) for track in payload.tracks])
            self._state.track_list = TrackList(
                offset=current.offset,
                limit=max(current.limit, len(items)),
                total=len(items),
                items=items,
            )
            self._update_sequence(payload)

    def _handle_tracks_removed(self, payload: TracksRemovedEvent) -> None:
        with self._lock:
            current = self._state.track_list
            items: List[Track] = list(current.items)
            for idx in sorted(set(payload.indices), reverse=True):
                if 0 <= idx < len(items):
                    items.pop(idx)
            self._state.track_list = TrackList(
                offset=current.offset,
                limit=min(current.limit, len(items)) if current.limit else len(items),
                total=len(items),
                items=items,
            )
            self._update_sequence(payload)

    def _handle_volume_changed(self, payload: VolumeChangedEvent) -> None:
        with self._lock:
            device_volume = self._state.device_volume.model_copy(deep=True)
            device_volume.current_volume = payload.volume
            self._state.device_volume = device_volume
            self._update_sequence(payload)

    def get_snapshot(self) -> State:
        with self._lock:
            return self._state.copy()

    def unsubscribe_all(self) -> None:
        with self._lock:
            for subscription in self._subscriptions:
                subscription.unsubscribe()
            self._subscriptions.clear()

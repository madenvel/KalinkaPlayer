import asyncio
import logging

from functools import partial
import threading
from typing import Optional

from kalinka_plugin_sdk.events import AnyEventPayload, EventType, StateReplayEvent
from kalinka_server.state_manager import StateManager
from pydantic import BaseModel

from .async_common import EventListener

logger = logging.getLogger(__name__.split(".")[-1])


class WireEvent(BaseModel):
    event_type: EventType
    payload: AnyEventPayload


class EventStream:
    def __init__(self, event_listener: EventListener, state_manager: StateManager):
        self.queue = asyncio.Queue()
        self.replayed = threading.Event()
        self.temporary_queue = []
        self.subscriptions = event_listener.subscribe_all(
            {event_type: partial(self._callback) for event_type in EventType}
        )
        initial_state = state_manager.get_snapshot()
        self.queue.put_nowait(
            WireEvent(
                event_type=EventType.StateReplay,
                payload=StateReplayEvent(
                    state=initial_state.player_state,
                    track_list=initial_state.track_list,
                    playback_mode=initial_state.playback_mode,
                    sequence=initial_state.sequence,
                ),
            )
        )
        self.first_sequence = initial_state.sequence
        self.replayed.set()

    async def get_last_event(self) -> Optional[WireEvent]:
        try:
            last_event = await asyncio.wait_for(self.queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return None

        return last_event

    def _callback(self, event: AnyEventPayload):
        if not self.replayed.is_set():
            self.temporary_queue.append(event)
            return

        if self.temporary_queue:
            for e in self.temporary_queue:
                if isinstance(e, AnyEventPayload) and e.sequence > self.first_sequence:
                    self._enqueue_event(WireEvent(event_type=e.event_type, payload=e))
            self.temporary_queue.clear()
            self.temporary_queue = []

        self._enqueue_event(WireEvent(event_type=event.event_type, payload=event))

    def _enqueue_event(self, event: WireEvent):
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull as e:
            logger.error(f"Failed to enqueue event: {e}")
            pass

    def close(self):
        for subscription in self.subscriptions.values():
            subscription.unsubscribe()

import asyncio
import logging

from functools import partial
from typing import Optional

from kalinka_plugin_sdk.events import AnyEventPayload, EventType
from pydantic import BaseModel

from .async_common import EventListener

logger = logging.getLogger(__name__.split(".")[-1])


class WireEvent(BaseModel):
    event_type: EventType
    payload: AnyEventPayload


class EventStream:
    def __init__(self, event_listener: EventListener):
        self.subscriptions = []
        self.queue = asyncio.Queue()
        self.replay_complete = False
        for event_type in EventType:
            self.subscriptions.append(
                event_listener.subscribe(event_type, partial(self._callback))
            )

    async def get_last_event(self) -> Optional[WireEvent]:
        try:
            last_event = await asyncio.wait_for(self.queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return None

        return last_event

    def _callback(self, event: AnyEventPayload):
        # Ignore state changing events
        # until replay event is issued
        if self.replay_complete is False and event.event_type in [
            EventType.StateChanged,
            EventType.TracksAdded,
            EventType.TracksRemoved,
        ]:
            return

        if event.event_type == EventType.StateReplay:
            if self.replay_complete is False:
                self.replay_complete = True
            else:
                return

        try:
            self.queue.put_nowait(WireEvent(event_type=event.event_type, payload=event))
        except asyncio.QueueFull as e:
            logger.error(f"Failed to enqueue event: {e}")
            pass

    def close(self):
        for subscription in self.subscriptions:
            subscription.unsubscribe()

from functools import partial
from src.events import EventType
from src.rpiasync import EventListener
from queue import Empty, Queue
import logging

logger = logging.getLogger(__name__.split(".")[-1])


class EventStream:
    def __init__(self, event_listener: EventListener):
        self.subscriptions = []
        self.queue = Queue()
        self.replay_complete = False
        for event in EventType:
            self.subscriptions.append(
                event_listener.subscribe(event, partial(self._callback, event))
            )

    def get_event(self) -> dict:
        try:
            last_event = self.queue.get(block=True, timeout=5)
        except Empty as e:
            return None

        return last_event

    def _callback(self, event_type: EventType, *args, **kwargs):
        # Ignore state changing events
        # until replay event is issued
        if self.replay_complete is False and event_type in [
            EventType.StateChanged,
            EventType.TracksAdded,
            EventType.TracksRemoved,
        ]:
            return

        if event_type == EventType.StateReplay:
            if self.replay_complete is False:
                self.replay_complete = True
            else:
                return

        self.queue.put(
            {
                "event_type": event_type.value,
                "args": args,
                "kwargs": kwargs,
            }
        )

    def close(self):
        for subscription in self.subscriptions:
            subscription.unsubscribe()

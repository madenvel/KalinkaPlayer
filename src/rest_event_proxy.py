from functools import partial
from threading import Condition
from src.events import EventType
from src.rpiasync import EventListener
from queue import Empty, Queue
import logging

logger = logging.getLogger(__name__)


class EventStream:
    def __init__(self, event_listener: EventListener):
        self.subscriptions = []
        for event in EventType:
            self.subscriptions.append(
                event_listener.subscribe(event, partial(self._callback, event))
            )
        self.queue = Queue()

    def get_event(self) -> dict:
        try:
            last_event = self.queue.get(block=True, timeout=5)
        except Empty as e:
            return None

        return last_event

    def _callback(self, event_type: EventType, *args, **kwargs):
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

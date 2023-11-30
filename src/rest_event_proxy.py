from functools import partial
from threading import Condition
from src.events import EventType
from src.rpiasync import EventListener
import logging

logger = logging.getLogger(__name__)


class EventStream:
    def __init__(self, event_listener: EventListener):
        for event in EventType:
            event_listener.subscribe(event, partial(self._callback, event))
        self.condition = Condition()
        self.last_event = None

    def wait_for_state_change(self) -> dict:
        with self.condition:
            self.last_event = None
            self.condition.wait(timeout=5)

        return self.last_event

    def _callback(self, event_type: EventType, *args, **kwargs):
        with self.condition:
            self.last_event = {
                "event_type": event_type.value,
                "args": args,
                "kwargs": kwargs,
            }
            self.condition.notify_all()

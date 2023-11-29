from functools import partial
from src.events import EventType

from uuid import uuid4
from queue import Queue

from src.rpiasync import EventListener


class EventStream:
    def __init__(self, event_listener: EventListener):
        self.queues = {}
        self.event_listener = event_listener

    def open_stream(self, event_types: list[EventType]):
        subscription_id = str(uuid4())
        self.queues[subscription_id] = Queue()
        for event_type in event_types:
            self.event_listener.subscribe(
                event_type, partial(self._callback, subscription_id, event_type)
            )
        return subscription_id

    def get_event(self, subscrition_id):
        if subscrition_id not in self.queues:
            return None

        return self.queues[subscrition_id].get(block=True)

    def _callback(self, subscription_id, event_type, *args, **kwargs):
        if subscription_id not in self.queues:
            return

        msg = {
            "event_type": event_type,
            "args": args,
        }
        for key, value in kwargs.items():
            msg[key] = value
        self.queues[subscription_id].put(msg)

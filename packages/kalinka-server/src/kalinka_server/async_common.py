import logging
import time
from typing import Callable
from uuid import UUID, uuid4
from abc import ABC, abstractmethod
from functools import partial, wraps
from threading import Lock, Thread
from itertools import count

from kalinka_plugin_sdk.events import AnyEventPayload, EventType

logger = logging.getLogger(__name__.split(".")[-1])


def timeit(func):
    @wraps(func)
    def timeit_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        # first item in the args, ie `args[0]` is `self`
        print(f"{func.__name__}{args} {kwargs}: {total_time:.4f} s")
        return result

    return timeit_wrapper


class AsyncLoop(ABC):

    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.thread = Thread(target=self._loop)
        self.thread.start()

    def _loop(self):
        while True:
            e = self.queue.get(block=True)
            if e == "terminate":
                break
            self.process(e)

    def terminate(self):
        self.queue.put("terminate")
        self.thread.join()

    @abstractmethod
    def process(self, e):
        pass


def pickle(obj_method_name, *args, **kwargs):
    return {
        "method": obj_method_name,
        "args": args,
        "kwargs": kwargs,
    }


def unpickle(obj, data):
    method = getattr(obj, data["method"], None)
    if method is not None:
        args = data["args"]
        kwargs = data["kwargs"]
        return partial(method, *args, **kwargs)

    return None


class Subscription:
    uuid: UUID
    event_name: str

    def __init__(self, uuid, event_name, event_listener):
        self.uuid = uuid
        self.event_name = event_name
        self.event_listener = event_listener

    def unsubscribe(self):
        self.event_listener.unsubscribe(self.event_name, self.uuid)


class EventListener(AsyncLoop):
    def __init__(self, queue):
        self.subscribers: dict[EventType, list[dict]] = {}
        self._lock = Lock()
        self._sequence = count(start=1)
        super().__init__(queue)

    def subscribe(
        self, event_type: EventType, callback: Callable[[AnyEventPayload], None]
    ) -> Subscription:
        with self._lock:
            return self._subscribe(event_type, callback)

    def _subscribe(
        self, event_type: EventType, callback: Callable[[AnyEventPayload], None]
    ) -> Subscription:
        uuid = uuid4()
        self.subscribers.setdefault(event_type, [])
        self.subscribers[event_type].append({"uuid": uuid, "cb": callback})
        return Subscription(uuid, event_type, self)

    def subscribe_all(self, map) -> dict[EventType, Subscription]:
        subscriptions = {}
        with self._lock:
            for k, v in map.items():
                subscriptions[k] = self._subscribe(k, v)

        return subscriptions

    def unsubscribe(self, event_type: EventType, uuid: UUID):
        with self._lock:
            for subscriber in list(self.subscribers.get(event_type, [])):
                if subscriber["uuid"] == uuid:
                    self.subscribers[event_type].remove(subscriber)

    def process(self, e: AnyEventPayload):
        if not getattr(e, "sequence", 0):
            # Ensure every event is assigned a sequence even if emitter missed it.
            setattr(e, "sequence", next(self._sequence))
        event = e.event_type
        with self._lock:
            subscribers = list(self.subscribers.get(event, []))
        for subscriber in subscribers:
            try:
                callback = subscriber["cb"]
                callback(e)
            except Exception as ex:
                logger.warning(
                    f"Exception caught while processing event {event}, exception: {ex}"
                )


class EventEmitter:
    def __init__(self, queue):
        self.queue = queue
        self._sequence_lock = Lock()
        self._sequence = count(start=1)

    def dispatch(self, payload: AnyEventPayload) -> None:
        # Assign a monotonically increasing sequence to each payload.
        with self._sequence_lock:
            payload.sequence = next(self._sequence)
        self.queue.put(payload)

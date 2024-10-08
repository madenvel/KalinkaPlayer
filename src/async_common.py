from abc import ABC, abstractmethod
from functools import partial
from threading import Thread

from multiprocessing import Queue, Process

import logging

from functools import wraps
import time
from uuid import UUID, uuid4

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
    queue = None

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


class RequestProxy:
    def __init__(self, queue: Queue, cls, process: Process):
        self._init_functions(cls)
        self.queue = queue
        self.process = process

    def terminate(self):
        self.queue.put("terminate")
        self.process.join()

    def _init_functions(self, cls):
        attrs = [a for a in dir(cls) if not a.startswith("_") and a != "terminate"]
        for attr in attrs:
            setattr(self, attr, partial(self._default_func, attr))

    def _default_func(self, name, *args, **kwargs):
        self.queue.put(pickle(name, *args, **kwargs))


class RequestExecutor:
    def __init__(self, queue: Queue, obj: any):
        self.queue = queue
        self.obj = obj

    def run(self):
        while True:
            data = self.queue.get(block=True)
            if data == "terminate":
                break

            op = unpickle(self.obj, data)

            if op is not None:
                op()
            else:
                logger.warn(f"Failed to unpickle, data={data}")


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
        self.subscribers = {}
        super().__init__(queue)

    def subscribe(self, event_name, callback) -> UUID:
        uuid = uuid4()
        self.subscribers.setdefault(event_name, [])
        self.subscribers[event_name].append({"uuid": uuid, "cb": callback})
        return Subscription(uuid, event_name, self)

    def subscribe_all(self, map):
        for k, v in map.items():
            self.subscribe(k, v)

    def unsubscribe(self, event_name, uuid):
        for subscriber in self.subscribers.get(event_name, []):
            if subscriber["uuid"] == uuid:
                self.subscribers[event_name].remove(subscriber)

    def process(self, e):
        event = e["event_name"]
        for subscriber in self.subscribers.get(event, []):
            try:
                callback = subscriber["cb"]
                callback(*e["args"], **e["kwargs"])
            except Exception as ex:
                logger.warn(
                    f"Exception caught while processing event {event}, exception: {ex}"
                )


class EventEmitter:
    def __init__(self, queue):
        self.queue = queue

    def dispatch(self, event_name, *args, **kwargs):
        self.queue.put({"event_name": event_name, "args": args, "kwargs": kwargs})


def run_on_process(cls):
    def process(request_queue, event_queue, cls):
        obj = cls(EventEmitter(event_queue))
        executor = RequestExecutor(request_queue, obj)
        executor.run()
        obj.terminate()

    request_queue = Queue()
    event_queue = Queue()
    p = Process(
        target=process,
        name=cls.__name__,
        args=(request_queue, event_queue, cls),
    )
    p.start()

    return [RequestProxy(request_queue, cls, p), EventListener(event_queue)]

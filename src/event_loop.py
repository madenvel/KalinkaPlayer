import logging
from queue import Queue
import threading

from src.async_common import AsyncLoop


class Future:
    def __init__(self):
        self._done = threading.Event()
        self._result = None

    def set(self, result):
        self._result = result
        self._done.set()

    def get(self):
        self._done.wait()
        return self._result

    def done(self):
        return self._done.is_set()


def enqueue(func):
    def async_task(self, *args, **kwargs) -> Future:
        def task():
            return func(self, *args, **kwargs)

        future = Future()
        self.enqueue((task, future))
        return future

    return async_task


class AsyncExecutor(AsyncLoop):
    def __init__(self):
        AsyncLoop.__init__(self, Queue())

    def process(self, e):
        if e is not None:
            e[1].set(e[0]())
        else:
            logging.warn("Not a function")

    def enqueue(self, task):
        self.queue.put(task)

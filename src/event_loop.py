import logging
from threading import Thread
from queue import Queue

from src.rpiasync import AsyncLoop


def enqueue(func):
    def async_task(self, *args, **kwargs):
        def task():
            func(self, *args, **kwargs)

        self.enqueue(task)

    return async_task


class AsyncExecutor(AsyncLoop):
    def __init__(self):
        AsyncLoop.__init__(self, Queue())

    def process(self, e):
        if e is not None:
            e()
        else:
            logging.warn("Not a function")

    def enqueue(self, task):
        self.queue.put(task)

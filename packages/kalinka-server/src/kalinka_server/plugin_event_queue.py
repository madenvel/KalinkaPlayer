import queue
import threading
from typing import Callable
from kalinka_plugin_sdk.api import EventListenerAPI, SubscriptionHandle
from kalinka_plugin_sdk.events import EventType, AnyEventPayload
from .async_common import EventListener

import logging

logger = logging.getLogger(__name__.split(".")[-1])


class PluginEventQueue(EventListenerAPI):
    def __init__(self, event_listener: EventListener):
        self.__event_listener = event_listener
        self.__event_queue = queue.Queue()
        self.__worker_thread = threading.Thread(target=self.__worker, daemon=True)
        self.__running = True
        self.__worker_thread.start()

    def __worker(self):
        """Worker thread that processes events from the queue."""
        while self.__running:
            try:
                # Get an event from the queue with a timeout to allow checking __running
                event_data = self.__event_queue.get(timeout=1.0)
                if event_data is None:  # Sentinel value to stop processing
                    break

                handler, payload = event_data
                try:
                    handler(payload)
                except Exception as e:
                    logger.exception("Exception in event handler: %s", e)
                finally:
                    self.__event_queue.task_done()
            except queue.Empty:
                continue  # Timeout occurred, check __running and continue

    def subscribe(
        self, topic: EventType, handler: Callable[[AnyEventPayload], None]
    ) -> SubscriptionHandle:
        def queue_handler(payload: AnyEventPayload):
            """Put the event into the queue for processing by the worker thread."""
            self.__event_queue.put((handler, payload))

        return self.__event_listener.subscribe(topic, queue_handler)

    def shutdown(self):
        """Shutdown the event queue and worker thread."""
        self.__running = False
        self.__event_queue.put(None)  # Sentinel value to wake up worker
        self.__worker_thread.join(timeout=5.0)  # Wait up to 5 seconds for shutdown

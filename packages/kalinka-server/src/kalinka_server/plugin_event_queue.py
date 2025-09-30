from concurrent.futures import ThreadPoolExecutor
import queue
from typing import Callable
from kalinka_plugin_sdk.api import EventListenerAPI, SubscriptionHandle
from kalinka_plugin_sdk.events import EventType
from .async_common import EventListener

import logging

logger = logging.getLogger(__name__.split(".")[-1])


class KalinkaPluginEventQueue(EventListenerAPI):
    def __init__(self, event_listener: EventListener, max_workers: int = 1):
        self.__event_listener = event_listener
        self.__thread_pool_executor = ThreadPoolExecutor(max_workers=max_workers)

    def subscribe(self, topic: EventType, handler: Callable) -> SubscriptionHandle:

        def wrapper(*args, **kwargs):
            try:
                handler(*args, **kwargs)
            except Exception as e:
                logger.exception("Exception in event handler: %s", e)

        return self.__event_listener.subscribe(
            topic,
            lambda *args, **kwargs: self.__thread_pool_executor.submit(
                wrapper, *args, **kwargs
            ),
        )

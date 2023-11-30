import requests
import threading

from typing import Callable, Dict
from src.events import EventType

import json


class SSEEventListener:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}/queue/events"
        self.callbacks: Dict[EventType, Callable] = {}
        self.running = False

    def subscribe_all(self, map: Dict[EventType, Callable]):
        for event_type, callback in map.items():
            self.subscribe(event_type, callback)

    def subscribe(self, event_type: EventType, callback: Callable):
        self.callbacks.setdefault(event_type, [])
        self.callbacks[event_type].append(callback)

    def start(self):
        self.running = True
        threading.Thread(target=self._listen).start()

    def stop(self):
        self.running = False

    def _listen(self):
        response = requests.get(self.url, stream=True)
        for line in response.iter_lines():
            if not self.running:
                break
            if line:
                message = json.loads(line.decode("utf-8"))
                event_type = EventType(message["event_type"])
                for callback in self.callbacks.get(event_type, []):
                    callback(*message["args"], **message["kwargs"])

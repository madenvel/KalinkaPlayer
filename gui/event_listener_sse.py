import requests
import threading

from typing import Callable, Dict

# from src.events import EventType
from enum import Enum

import json


class EventType(Enum):
    Playing = "playing"
    Paused = "paused"
    Stopped = "stopped"
    Progress = "current_progress"
    TrackChanged = "change_track"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "track_added"
    TracksRemoved = "track_removed"


class State(Enum):
    IDLE = 0
    READY = 1
    BUFFERING = 2
    PLAYING = 3
    PAUSED = 4
    FINISHED = 5
    STOPPED = 6
    ERROR = 7


class SSEEventListener:
    def __init__(self, host, port):
        self.url = f"http://{host}:{port}/queue/events"
        self.callbacks: Dict[EventType, Callable] = {}

    def subscribe_all(self, map: Dict[EventType, Callable]):
        for event_type, callback in map.items():
            self.subscribe(event_type, callback)

    def subscribe(self, event_type: EventType, callback: Callable):
        self.callbacks.setdefault(event_type, [])
        self.callbacks[event_type].append(callback)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        response = requests.get(self.url, stream=True)
        for line in response.iter_lines():
            if line:
                message = json.loads(line.decode("utf-8"))
                event_type = EventType(message["event_type"])
                for callback in self.callbacks.get(event_type, []):
                    callback(*message["args"], **message["kwargs"])

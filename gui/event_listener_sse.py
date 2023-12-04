import requests
import threading
import logging
import time

from typing import Callable, Dict

# from src.events import EventType
from enum import Enum

import json

logger = logging.getLogger(__name__)


class EventType(Enum):
    Playing = "playing"
    Paused = "paused"
    Stopped = "stopped"
    Progress = "current_progress"
    TrackChanged = "change_track"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "track_added"
    TracksRemoved = "track_removed"
    NetworkError = "network_error"
    ConnectionFailed = "connection_failed"
    ConnectionRestored = "connection_restored"


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
        connected_failed = False
        session = requests.Session()
        while True:
            try:
                response = session.get(self.url, stream=True, timeout=5)
                if connected_failed:
                    logger.info("Connection re-established")
                    connected_failed = False
                    self._generate_event(EventType.ConnectionRestored)
                for line in response.iter_lines():
                    self._process_event(line)
            except Exception as e:
                logger.warn(f"Connection error: {e}, retrying in 1s")
                connected_failed = True
                self._generate_event(EventType.ConnectionFailed)
                time.sleep(1)

    def _process_event(self, line: str):
        if line:
            message = json.loads(line.decode("utf-8"))
            event_type = EventType(message["event_type"])
            for callback in self.callbacks.get(event_type, []):
                callback(*message["args"], **message["kwargs"])

    def _generate_event(self, event_type: EventType):
        if event_type in [EventType.ConnectionFailed, EventType.ConnectionRestored]:
            for callback in self.callbacks.get(event_type, []):
                callback()

import time
from data_model.response_model import PlayerState
from src.rpiasync import EventListener
import threading
from queue import Queue
import json

import logging

logger = logging.getLogger(__name__)


class QobuzReporter:
    def __init__(self, qobuz_client):
        self.qobuz_client = qobuz_client
        self.current_state = None
        self.progress_started = 0
        self.mqueue = Queue()
        threading.Thread(target=self._sender_worker, daemon=True).start()

    def on_state_changed(self, state: str):
        state = PlayerState(**state)
        if state.current_track:
            # Track changed
            if (
                self.current_state
                and self.current_state.current_track
                and self.current_state.state == "PLAYING"
            ):
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(),
                    }
                )

            self.current_state = PlayerState(
                current_track=state.current_track.model_copy(), progress=0
            )
            self.progress_started = 0

        if state.state:
            if self.current_state != None:
                self.current_state.state = state.state
                if state.state == "PLAYING":
                    self.progress_started = self.current_state.progress
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingStart",
                            "params": self._make_start_report_message(),
                        }
                    )

                if state.state == "PAUSED" or state.state == "STOPPED":
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingEnd",
                            "params": self._make_end_report_message(),
                        }
                    )

        if state.progress and self.current_state:
            self.current_state.progress = state.progress

    def _make_start_report_message(self):
        if (
            self.current_state.current_track.id
            not in self.qobuz_client.track_url_response_cache
        ):
            raise Exception("Track not found in cache")

        track_cache = self.qobuz_client.track_url_response_cache[
            self.current_state.current_track.id
        ]

        return {
            "user_id": self.qobuz_client.user_id,
            "credential_id": self.qobuz_client.credential_id,
            "date": int(time.time()),
            "track_id": self.current_state.current_track.id,
            "format_id": track_cache["format_id"],
            "duration": 0,
            "online": True,
            "intent": "streaming",
            "local": False,
            "purchase": False,
            "sample": False,
            "seek": 0,
            "totalTrackDuration": track_cache["duration"],
        }

    def _make_end_report_message(self):
        track_id = self.current_state.current_track.id
        if track_id not in self.qobuz_client.track_url_response_cache:
            raise Exception("Track not found in cache")

        track_cache = self.qobuz_client.track_url_response_cache[track_id]

        return {
            "user_id": self.qobuz_client.user_id,
            "credential_id": self.qobuz_client.credential_id,
            "date": int(time.time()),
            "track_id": self.current_state.current_track.id,
            "format_id": track_cache["format_id"],
            "duration": int(self.current_state.progress - self.progress_started),
            "online": True,
            "intent": "streaming",
            "local": False,
            "purchase": False,
            "sample": False,
            "seek": 0,
        }

    def _sender_worker(self):
        while True:
            message = self.mqueue.get()
            response = self.qobuz_client.session.post(
                self.qobuz_client.base + message["endpoint"],
                params={"events": json.dumps(message["params"])},
            )

            if not response.ok:
                logger.warn('Failed to send event to Qobuz: "%s"', response.text)

            logger.info(
                f"Sent event to Qobuz: {message['endpoint']}, message={message['params']}, status: {response.json()['status']}"
            )

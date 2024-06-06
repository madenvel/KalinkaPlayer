import time
from data_model.response_model import PlayerState
import threading
from queue import Queue
import json

import logging

logger = logging.getLogger(__name__)


class QobuzReporter:
    def __init__(self, qobuz_client):
        self.qobuz_client = qobuz_client
        self.current_state = None
        self.state_update_time = 0
        self.mqueue = Queue()
        threading.Thread(target=self._sender_worker, daemon=True).start()

    def on_state_changed(self, state: str):
        state = PlayerState(**state)
        if state.current_track:
            # Track changed
            if self.current_state and self.current_state.state == "PLAYING":
                current_time = time.monotonic_ns()
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        # We estimate the time here there's no state update sent
                        # for the old context from playqueue at the moment.
                        # This number is not entirely accurate and doesn't include
                        # buffering time of the next track due to gapless playback.
                        # However, in most cases it is more than enough.
                        "params": self._make_end_report_message(
                            (current_time - self.state_update_time) / 1_000_000
                        ),
                    }
                )

            self.current_state = state.model_copy()

        if state.state and self.current_state:
            if state.state == "PLAYING":
                self.position_started = self.current_state.position
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
                        "params": self._make_end_report_message(
                            duration=(state.position - self.current_state.position)
                        ),
                    }
                )

        self.current_state.state = state.state
        self.current_state.position = state.position
        self.state_update_time = time.monotonic_ns()

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

    def _make_end_report_message(self, duration):
        if duration < 0:
            logger.warn("Negative for qobuz report end message, duration: %d", duration)
            duration = 0

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
            "duration": int(duration / 1000),
            "online": True,
            "intent": "streaming",
            "local": False,
            "purchase": False,
            "sample": False,
            "seek": 0,
        }

    def _sender_worker(self):
        while True:
            try:
                message = self.mqueue.get()
                response = self.qobuz_client.session.post(
                    self.qobuz_client.base + message["endpoint"],
                    params={"events": json.dumps(message["params"])},
                )

                if not response.ok:
                    logger.warn('Failed to send event to Qobuz: "%s"', response.text)
                else:
                    logger.info(
                        f"Sent event to Qobuz: {message['endpoint']}, message={message['params']}, status: {response.json()['status']}"
                    )
            except Exception as e:
                logger.warn("Exception while sending event to Qobuz:", e)

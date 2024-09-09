import time
from data_model.response_model import PlayerState
import threading
from queue import Queue
import json

import logging

logger = logging.getLogger(__name__.split(".")[-1])


class QobuzReporter:
    def __init__(self, qobuz_client):
        self.qobuz_client = qobuz_client
        self.current_state = None
        self.mqueue = Queue()
        threading.Thread(target=self._sender_worker, daemon=True).start()

    def _get_played_time_sofar(self):
        time_played = (time.monotonic_ns() - self.current_state.timestamp) / 1_000_000
        time_played = int(time_played / 1000)
        if time_played > self.current_state.current_track.duration:
            time_played = self.current_state.current_track.duration
        return time_played

    def on_state_changed(self, state: str):
        state = PlayerState(**state)
        if state.state == "PLAYING":
            if (
                self.current_state
                and self.current_state.current_track
                and self.current_state.current_track.id != state.current_track.id
            ):
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(
                            self._get_played_time_sofar()
                        ),
                    }
                )
            self.current_state = state.model_copy()
            self.mqueue.put(
                {
                    "endpoint": "track/reportStreamingStart",
                    "params": self._make_start_report_message(),
                }
            )
        elif state.state in ["STOPPED", "PAUSED"]:
            if (
                self.current_state
                and self.current_state.current_track
                and self.current_state.state == "PLAYING"
            ):
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(
                            self._get_played_time_sofar()
                        ),
                    }
                )
                self.current_state = state.model_copy()

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

    def _make_end_report_message(self, duration_s: int):
        if duration_s < 0:
            logger.warn(
                "Negative for qobuz report end message, duration: %d", duration_s
            )
            duration_s = 0

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
            "duration": duration_s,
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

                if not response.is_success:
                    logger.warn('Failed to send event to Qobuz: "%s"', response.text)
                else:
                    logger.info(
                        f"Sent event to Qobuz: {message['endpoint']}, message={message['params']}, status: {response.json()['status']}"
                    )
            except Exception as e:
                logger.warn("Exception while sending event to Qobuz:", e)

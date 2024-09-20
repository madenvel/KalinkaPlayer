import time
from data_model.response_model import PlayerState
import threading
from queue import Queue
import json

import logging

logger = logging.getLogger(__name__.split(".")[-1])

# Reports seem to be rate limited, limit to 1 event per second
REPORTS_PER_SEC_LIMIT = 3


# Report events to Qobuz
# streamingStart - reported when the track changes and the player starts playing only
# streamingEnd - reported when player stops playing the track, pauses or seeks to a new position.
#
# The value of the duration is the number of seconds the track was played since the last report,
# whether it was streamingStart or streamingEnd.
class QobuzReporter:
    def __init__(self, qobuz_client):
        self.qobuz_client = qobuz_client
        self.mqueue = Queue()
        self.last_report_time = 0
        self.current_track_id = None
        threading.Thread(target=self._sender_worker, daemon=True).start()

    def get_last_duration(self):
        report_time = time.monotonic_ns()
        time_played = int((report_time - self.last_report_time) / 1_000_000_000)
        self.last_report_time = report_time
        return time_played

    def on_state_changed(self, state: str):
        state = PlayerState(**state)
        # playing and current track != previous track
        # => report streaming start for new track, report streaming end for previous track
        # if playing and current track == previous track
        # => likely search request, report streaming end
        # if stopped or paused, report streaming end for current track

        if state.state == "PLAYING":
            if state.current_track.id != self.current_track_id:
                if self.current_track_id:
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingEnd",
                            "params": self._make_end_report_message(
                                self.current_track_id, self.get_last_duration()
                            ),
                        }
                    )
                self.current_track_id = state.current_track.id
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingStart",
                        "params": self._make_start_report_message(
                            state.current_track.id
                        ),
                    }
                )
                self.get_last_duration()
                self.current_track_id = state.current_track.id
            else:
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(
                            self.current_track_id, self.get_last_duration()
                        ),
                    }
                )
        elif state.state in ["STOPPED", "PAUSED", "ERROR"]:
            if self.current_track_id:
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(
                            self.current_track_id, self.get_last_duration()
                        ),
                    }
                )
                self.current_track_id = None

    def _make_start_report_message(self, track_id: int):
        if track_id not in self.qobuz_client.track_url_response_cache:
            raise Exception("Track not found in cache")

        track_cache = self.qobuz_client.track_url_response_cache[track_id]

        return {
            "user_id": self.qobuz_client.user_id,
            "credential_id": self.qobuz_client.credential_id,
            "date": int(time.time()),
            "track_id": track_id,
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

    def _make_end_report_message(self, track_id: int, duration_s: int):
        if duration_s < 0:
            logger.warn(
                "Negative for qobuz report end message, duration: %d", duration_s
            )
            duration_s = 0

        if track_id not in self.qobuz_client.track_url_response_cache:
            raise Exception("Track not found in cache")

        track_cache = self.qobuz_client.track_url_response_cache[track_id]

        return {
            "user_id": self.qobuz_client.user_id,
            "credential_id": self.qobuz_client.credential_id,
            "date": int(time.time()),
            "track_id": track_id,
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
                    params={"events": json.dumps([message["params"]])},
                )

                self.mqueue.task_done()

                if not response.is_success:
                    logger.warn('Failed to send event to Qobuz: "%s"', response.text)
                else:
                    logger.info(
                        f"Sent event to Qobuz: {message['endpoint']}, message={message['params']}, status: {response.json()['status']}"
                    )
            except Exception as e:
                logger.warn("Exception while sending event to Qobuz:", e)

            time.sleep(REPORTS_PER_SEC_LIMIT)

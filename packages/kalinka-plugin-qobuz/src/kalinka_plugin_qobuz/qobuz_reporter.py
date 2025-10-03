import json
import logging
import threading
import time
from queue import Queue
from typing import Optional
from kalinka_plugin_sdk.datamodel import PlayerState, PlayerStateEnum
from kalinka_plugin_sdk.events import AnyEventPayload, StateChangedEvent

logger = logging.getLogger(__name__.split(".")[-1])

# Reports seem to be rate limited, limit to 1 event per second
REPORTS_PER_SEC_LIMIT = 3


class QobuzReporter:
    def __init__(self, qobuz_client):
        self.qobuz_client = qobuz_client
        self.mqueue = Queue()
        self.last_report_time = 0
        self.current_track_id: Optional[str] = None
        self._isRunning = True
        self.sender_job = threading.Thread(target=self._sender_worker, daemon=True)
        self.sender_job.start()

    def get_last_duration(self):
        report_time = time.monotonic_ns()
        time_played = int((report_time - self.last_report_time) / 1_000_000_000)
        self.last_report_time = report_time
        return time_played

    def on_state_changed(self, event: AnyEventPayload) -> None:
        """
        Handle player state changes for Qobuz reporting.

        This method only processes tracks from the 'qobuz' source and ignores all others.
        It properly handles transitions between Qobuz and non-Qobuz tracks by ending
        Qobuz tracking when switching to a different source.
        """
        if not isinstance(event, StateChangedEvent):
            logger.warning("Expected StateChangedEvent, got %s", type(event))
            return

        player_state = event.state
        # playing and current track != previous track
        # => report streaming start for new track, report streaming end for previous track
        # if playing and current track == previous track
        # => likely search request, report streaming end
        # if stopped or paused, report streaming end for current track

        if player_state.state == PlayerStateEnum.PLAYING:
            track = player_state.current_track

            if track is not None and track.id.source == "qobuz":
                # This is a Qobuz track - extract the raw track ID for Qobuz reporting
                logger.info("Processing Qobuz track for report: %s", track.id)
                current_qobuz_track_id = track.id.id

                if current_qobuz_track_id != self.current_track_id:
                    # Track change detected
                    if self.current_track_id is not None:
                        # Report end of previous Qobuz track
                        self.mqueue.put(
                            {
                                "endpoint": "track/reportStreamingEnd",
                                "params": self._make_end_report_message(
                                    self.current_track_id, self.get_last_duration()
                                ),
                            }
                        )
                    # Start reporting for new Qobuz track
                    self.current_track_id = current_qobuz_track_id
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingStart",
                            "params": self._make_start_report_message(
                                current_qobuz_track_id
                            ),
                        }
                    )
                    self.get_last_duration()
                elif self.current_track_id is not None:
                    # Same Qobuz track playing - report streaming end (likely search/seek)
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingEnd",
                            "params": self._make_end_report_message(
                                self.current_track_id, self.get_last_duration()
                            ),
                        }
                    )
            else:
                # Track is not from Qobuz (different source or no track)
                # If we were previously tracking a Qobuz track, report its end
                if self.current_track_id is not None:
                    self.mqueue.put(
                        {
                            "endpoint": "track/reportStreamingEnd",
                            "params": self._make_end_report_message(
                                self.current_track_id, self.get_last_duration()
                            ),
                        }
                    )
                    self.current_track_id = None
        elif player_state.state in ["STOPPED", "PAUSED", "ERROR"]:
            # Player stopped/paused/error - report end of any currently tracked Qobuz track
            if self.current_track_id is not None:
                self.mqueue.put(
                    {
                        "endpoint": "track/reportStreamingEnd",
                        "params": self._make_end_report_message(
                            self.current_track_id, self.get_last_duration()
                        ),
                    }
                )
                self.current_track_id = None

    def _make_start_report_message(self, track_id: str | int):
        # Ensure track_id is int for the report
        if isinstance(track_id, str):
            try:
                track_id_int = int(track_id)
            except ValueError:
                raise Exception(f"Track id '{track_id}' is not convertible to int")
        else:
            track_id_int = track_id
        if track_id not in self.qobuz_client.track_url_response_cache:
            raise Exception("Track not found in cache")
        track_cache = self.qobuz_client.track_url_response_cache[track_id]
        return {
            "user_id": self.qobuz_client.user_id,
            "credential_id": self.qobuz_client.credential_id,
            "date": int(time.time()),
            "track_id": track_id_int,
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

    def _make_end_report_message(self, track_id: str | int, duration_s: int):
        # Ensure track_id is int for the report
        if isinstance(track_id, str):
            try:
                track_id_int = int(track_id)
            except ValueError:
                raise Exception(f"Track id '{track_id}' is not convertible to int")
        else:
            track_id_int = track_id
        if duration_s < 0:
            logger.warning(
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
            "track_id": track_id_int,
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
        while self._isRunning:
            try:
                message = self.mqueue.get()
                if message is None:
                    break

                response = self.qobuz_client.session.post(
                    self.qobuz_client.base + message["endpoint"],
                    params={"events": json.dumps([message["params"]])},
                )

                self.mqueue.task_done()

                if not getattr(response, "is_success", False):
                    logger.warning(
                        'Failed to send event to Qobuz: "%s"',
                        getattr(response, "text", ""),
                    )
                else:
                    logger.info(
                        "Sent event to Qobuz: %s, message=%s, status: %s",
                        message["endpoint"],
                        message["params"],
                        (
                            response.json().get("status")
                            if hasattr(response, "json")
                            else None
                        ),
                    )
            except Exception as e:
                logger.warning("Exception while sending event to Qobuz: %s", e)

            time.sleep(REPORTS_PER_SEC_LIMIT)

    def shutdown(self):
        self._isRunning = False
        self.mqueue.put(None)
        self.sender_job.join()

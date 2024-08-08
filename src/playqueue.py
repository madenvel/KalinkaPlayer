import logging
import time

from functools import partial
from typing import Optional
from threading import Timer, Thread

from collections import OrderedDict

from data_model.response_model import AudioInfo, PlayerState
from data_model.datamodel import Track

from src.event_loop import AsyncExecutor, enqueue
from src.async_common import EventEmitter
from src.inputmodule import TrackInfo
from src.events import EventType
from src.config import config

from native_player.native_player import (
    AudioPlayer,
    StreamState,
    AudioGraphNodeState,
    StreamInfo,
)


logger = logging.getLogger(__name__.split(".")[-1])

# enum State {
#   IDLE = 0,
#   READY,
#   BUFFERING,
#   PLAYING,
#   PAUSED,
#   FINISHED,
#   STOPPED,
#   ERROR
# };

# enum class AudioGraphNodeState {
#   ERROR = -1,
#   STOPPED,
#   PREPARING,
#   STREAMING,
#   PAUSED,
#   FINISHED,
#   SOURCE_CHANGED
# };

PREFETCH_TIME_MS = 5000


def to_audio_info(stream_info: StreamInfo):
    if stream_info is None:
        return None
    return AudioInfo(
        sample_rate=stream_info.format.sample_rate,
        channels=stream_info.format.channels,
        bits_per_sample=stream_info.format.bits_per_sample,
        duration_ms=stream_info.duration_ms,
    )


def to_state_name(state: AudioGraphNodeState) -> str:
    if state == AudioGraphNodeState.ERROR:
        return "ERROR"
    elif state == AudioGraphNodeState.STOPPED:
        return "STOPPED"
    elif state == AudioGraphNodeState.PREPARING:
        return "BUFFERING"
    elif state == AudioGraphNodeState.STREAMING:
        return "PLAYING"
    elif state == AudioGraphNodeState.PAUSED:
        return "PAUSED"
    elif state == AudioGraphNodeState.FINISHED:
        return "FINISHED"


class PlayQueue(AsyncExecutor):
    def __init__(self, event_emitter: EventEmitter):
        super().__init__()
        self.event_emitter = event_emitter
        self.track_player = AudioPlayer(config["output_device"]["alsa"]["device"])
        self.current_track_id = 0
        self.track_list: list[TrackInfo] = []

        self.timer_thread = None
        self.listen_state = True
        self.state_monitor = self.track_player.monitor()
        self.prepared_tracks = OrderedDict()

        self.state_update_thread = Thread(target=self._state_update_listener)
        self.state_update_thread.start()

    def __del__(self):
        self.terminate()

    def terminate(self):
        self.listen_state = False
        self.track_player.stop()
        self.state_monitor.stop()
        self.state_update_thread.join()
        super().terminate()

    def _state_update_listener(self):
        while self.listen_state:
            new_state = self.state_monitor.wait_state()
            logger.info(f"New state: {new_state}")
            self._process_state_update(new_state)

    @enqueue
    def _process_state_update(self, new_state):
        if new_state.state == AudioGraphNodeState.SOURCE_CHANGED:
            if self.prepared_tracks:
                item = self.prepared_tracks.popitem(last=False)
                self.current_track_id = item[0]
                self._request_more_tracks()
            return
        elif new_state.state == AudioGraphNodeState.FINISHED:
            self._cancel_prefetch_timer()
            new_state.state = AudioGraphNodeState.STOPPED
        elif new_state.state == AudioGraphNodeState.STREAMING:
            self._setup_prefetch_timer(new_state)
        elif new_state.state != AudioGraphNodeState.STREAMING:
            self._cancel_prefetch_timer()

        state_update_ts = time.monotonic_ns()
        position_diff = 0
        if new_state.state == AudioGraphNodeState.STREAMING:
            position_diff = int((state_update_ts - new_state.timestamp) / 1_000_000)

        self.event_emitter.dispatch(
            EventType.StateChanged,
            PlayerState(
                state=to_state_name(new_state.state),
                current_track=self.get_track_info(self.current_track_id),
                index=self.current_track_id,
                position=new_state.position + position_diff,
                message=new_state.message,
                audio_info=to_audio_info(new_state.stream_info),
                timestamp=state_update_ts,
            ).model_dump(exclude_none=True),
        )

    @enqueue
    def play(self, index=None):
        self._play_sync(index)

    def _play_sync(self, index):
        if len(self.track_list) == 0:
            return

        if index is not None and index not in range(0, len(self.track_list)):
            return

        if index is None:
            index = self.current_track_id

        track_url = self._setup_track_to_play(index)
        if track_url is None:
            return

        self.prepared_tracks.clear()
        self.prepared_tracks[index] = track_url
        self.track_player.play(track_url)

    @enqueue
    def play_next(self, index):
        if len(self.track_list) == 0:
            return

        if (
            index is not None
            and index not in range(0, len(self.track_list))
            or index in self.prepared_tracks
        ):
            return

        logger.info(f"Playing next track index={index}")

        track_url = self._setup_track_to_play(index)
        if track_url is None:
            return

        self.prepared_tracks[index] = track_url
        self.track_player.play_next(track_url)

    @enqueue
    def pause(self, paused: bool):
        self.track_player.pause(paused)

    @enqueue
    def next(self):
        self._play_sync(self.current_track_id + 1)

    @enqueue
    def prev(self):
        self._play_sync(self.current_track_id - 1)

    @enqueue
    def seek(self, positionMs: int):
        return self.track_player.seek(positionMs)

    @enqueue
    def stop(self):
        self.track_player.stop()

    @enqueue
    def add(self, tracks: list[TrackInfo]):
        index = len(self.track_list)
        self.track_list.extend(tracks)
        self.event_emitter.dispatch(
            EventType.TracksAdded,
            [self.get_track_info(i) for i in range(index, len(self.track_list))],
        )

        if index == 0:
            self._notify_track_change()

    @enqueue
    def remove(self, tracks: list[int]):
        if self.current_track_id in tracks:
            self.track_player.stop()

        prev_track_id = self.current_track_id

        tracks.sort(reverse=True)
        for track in tracks:
            if track in self.prepared_tracks:
                del self.prepared_tracks[track]

            if track < self.current_track_id:
                self.current_track_id -= 1

            del self.track_list[track]
        self.current_track_id = min(self.current_track_id, len(self.track_list) - 1)
        if self.current_track_id < 0:
            self.current_track_id = 0
        self.event_emitter.dispatch(EventType.TracksRemoved, tracks)
        if prev_track_id != self.current_track_id or prev_track_id in tracks:
            self._notify_track_change()

    def list(self, offset: int, limit: int):
        if offset not in range(0, len(self.track_list)):
            return {
                "offset": offset,
                "limit": limit,
                "total": len(self.track_list),
                "items": [],
            }

        return {
            "offset": offset,
            "limit": limit,
            "total": len(self.track_list),
            "items": [
                self.get_track_info(i)
                for i in range(offset, min(offset + limit, len(self.track_list)))
            ],
        }

    def get_track_info(self, index: int):
        if index not in range(0, len(self.track_list)):
            return None
        track_info: TrackInfo = self.track_list[index]
        return track_info.metadata.model_dump(exclude_unset=True)

    def get_state(self) -> PlayerState:
        stream_state = self.track_player.get_state()
        return PlayerState(
            state=to_state_name(stream_state.state),
            current_track=(
                self.get_track_info(self.current_track_id)
                if self.current_track_id in range(0, len(self.track_list))
                else None
            ),
            index=self.current_track_id,
            position=self._estimated_progress(stream_state),
            message=stream_state.message,
            audio_info=to_audio_info(stream_state.stream_info),
            timestamp=time.monotonic_ns(),
        )

    @enqueue
    def replay(self) -> PlayerState:
        stream_state = self.track_player.get_state()
        self.event_emitter.dispatch(
            EventType.StateReplay,
            PlayerState(
                state=to_state_name(stream_state.state),
                current_track=(
                    self.get_track_info(self.current_track_id)
                    if self.current_track_id in range(0, len(self.track_list))
                    else None
                ),
                index=self.current_track_id,
                position=self._estimated_progress(stream_state),
                message=stream_state.message,
                audio_info=to_audio_info(stream_state.stream_info),
            ).model_dump(exclude_none=True),
            self.list(0, len(self.track_list)),
        )

    @enqueue
    def clear(self):
        self._clear()

    def _clear(self):
        self.track_player.stop()
        self.track_urls = {}
        list_len = len(self.track_list)
        self.track_list = []
        self.current_track_id = 0
        self.event_emitter.dispatch(
            EventType.TracksRemoved,
            [i for i in range(list_len - 1, -1, -1)],
        )

    def _estimated_progress(self, stream_state: StreamState) -> int:
        if stream_state.state != AudioGraphNodeState.STREAMING:
            return stream_state.position

        progress = stream_state.position + int(
            (time.monotonic_ns() - stream_state.timestamp) / 1_000_000
        )

        return progress

    def _setup_track_to_play(self, index):
        track = self.track_list[index]
        if index not in self.prepared_tracks:
            try:
                track_info = track.link_retriever()
            except Exception as e:
                logger.warn("Failed to retrieve track link:", e)
                self.event_emitter.dispatch(EventType.NetworkError, str(e))
                return None

            return track_info.url

        return self.prepared_tracks[index]

    def _request_more_tracks(self):
        if self.current_track_id == len(self.track_list) - 1:
            self.event_emitter.dispatch(EventType.RequestMoreTracks)

    def _setup_prefetch_timer(self, state: StreamInfo):
        self._cancel_prefetch_timer()
        next_track_id = self.current_track_id + 1
        stream_info = state.stream_info
        if not stream_info:
            return

        time_to_prefetch_s = (
            stream_info.duration_ms - state.position - PREFETCH_TIME_MS
        ) / 1000

        if time_to_prefetch_s < 0:
            self.play_next(next_track_id)
            return

        logger.info(f"Prefetching next track in {time_to_prefetch_s} seconds")

        self.timer_thread = Timer(
            time_to_prefetch_s,
            partial(self.play_next, next_track_id),
        )
        self.timer_thread.start()

    def _cancel_prefetch_timer(self):
        if self.timer_thread is not None:
            self.timer_thread.cancel()
            self.timer_thread = None

    def _notify_track_change(self):
        self.event_emitter.dispatch(
            EventType.StateChanged,
            PlayerState(
                current_track=self.get_track_info(self.current_track_id),
                index=self.current_track_id,
                state=to_state_name(AudioGraphNodeState.STOPPED),
                position=0,
                timestamp=time.monotonic_ns(),
            ).model_dump(exclude_none=True),
        )

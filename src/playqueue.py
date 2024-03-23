from functools import partial
import logging
import time
from data_model.response_model import PlayerState
from src.event_loop import AsyncExecutor, enqueue

from src.rpiasync import EventEmitter

from src.inputmodule import TrackInfo
from native.rpiplayer import RpiAudioPlayer

from src.events import EventType
from src.states import State

from threading import Timer

logger = logging.getLogger(__name__)


def buffer_size(seconds: int, bits: int, sample_rate: int):
    bytes = bits / 8
    if bytes % 2 != 0:
        bytes += 1

    frame_size = bytes * 2
    frames_per_second = sample_rate * frame_size

    return frames_per_second * seconds


class PlayQueue(AsyncExecutor):
    def __init__(self, event_emitter: EventEmitter):
        super().__init__()
        self.event_emitter = event_emitter
        self.track_player = RpiAudioPlayer()
        self.track_player.set_state_callback(self._state_callback)

        self.current_track_id = 0
        self.current_context_id = -1
        self.context_map = {}
        self.track_list: list[TrackInfo] = []
        self.prefetched_tracks = {}
        self.current_progress = 0
        self.last_progress_update_ts = 0

        # properties
        self.advance = True
        self.state = State.IDLE
        self.timer_thread = None

    @enqueue
    def _state_callback(self, context_id, new_state, position):
        new_state = State(new_state)
        if new_state == State.READY:
            self.context_map[context_id] = self.track_player.get_audio_info(context_id)
            logger.info(f"Track ready: {self.context_map[context_id]}")

        if context_id != self.current_context_id and new_state != State.STOPPED:
            return

        if new_state == State.FINISHED:
            if self.current_track_id == len(self.track_list) - 1:
                new_state == State.STOPPED
            else:
                return self._finished_playing()

        if new_state == State.STOPPED:
            self.context_map.pop(context_id, None)

        self.state = new_state
        self.current_progress = position
        self.last_progress_update_ts = time.monotonic_ns()
        self.event_emitter.dispatch(
            EventType.StateChanged,
            PlayerState(
                state=new_state.name,
                position=position,
            ).model_dump(exclude_none=True),
        )

        if new_state == State.PLAYING:
            self._setup_prefetch_timer()
        else:
            self._cancel_prefetch_timer()

    def _setup_prefetch_timer(self):
        self._cancel_prefetch_timer()
        next_track_id = self.current_track_id + 1
        audio_info = self.context_map[self.current_context_id]
        time_to_prefetch_s = (
            audio_info["duration_ms"] - self.current_progress - 5000
        ) / 1000
        if time_to_prefetch_s < 0:
            return
        logger.info(f"Prefetching next track in {time_to_prefetch_s} seconds")
        self.timer_thread = Timer(
            time_to_prefetch_s,
            partial(self._prepare_track, next_track_id),
        )
        self.timer_thread.start()

    def _cancel_prefetch_timer(self):
        if self.timer_thread is not None:
            self.timer_thread.cancel()
            self.timer_thread = None

    @enqueue
    def _finished_playing(self):
        if self.advance is True:
            self.next()

    def _notify_track_change(self):
        self.state = State.IDLE
        self.event_emitter.dispatch(
            EventType.StateChanged,
            PlayerState(
                current_track=self.get_track_info(self.current_track_id),
                index=self.current_track_id,
                state=State.IDLE.name,
                position=0,
            ).model_dump(exclude_none=True),
        )

    @enqueue
    def _prepare_track(self, index):
        if index not in self.prefetched_tracks and index < len(self.track_list):
            logger.info(f"Prepare next track index {index}")
            link_retriever = self.track_list[index].link_retriever
            track_id = self.track_list[index].metadata.id
            try:
                track_info = link_retriever()
            except Exception as e:
                logger.warn("Failed to pre-fetch track:", e)
                return
            if track_id == self.track_list[index].metadata.id:
                context_id = self.track_player.prepare(
                    track_info.url,
                    buffer_size(10, track_info.bit_depth, track_info.sample_rate),
                    64 * 1024,
                )
                self.prefetched_tracks[index] = context_id

    def _play_track(self):
        track = self.track_list[self.current_track_id]
        context_id = self.prefetched_tracks.pop(
            self.current_track_id,
            -1,
        )
        if context_id == -1:
            try:
                track_info = track.link_retriever()
            except Exception as e:
                logger.warn("Failed to retrieve track link:", e)
                self.event_emitter.dispatch(EventType.NetworkError, str(e))
                self.stop()
                return

            context_id = self.track_player.prepare(
                track_info.url,
                buffer_size(10, track_info.bit_depth, track_info.sample_rate),
                64 * 1024,
            )
        self.context_map.pop(self.current_context_id, None)
        self.current_context_id = context_id
        self.track_player.play(self.current_context_id)
        for stale_context_id in self.prefetched_tracks.values():
            self.track_player.remove_context(stale_context_id)
        self.prefetched_tracks = {}

    @enqueue
    def play(self, index=None):
        if len(self.track_list) == 0:
            return

        if index is not None and index not in range(0, len(self.track_list)):
            return

        prev_index = self.current_track_id

        if index is not None:
            self.current_track_id = index

        if prev_index != self.current_track_id:
            self._notify_track_change()

        self._play_track()
        self._request_more_tracks()

    @enqueue
    def pause(self, paused: bool):
        self.track_player.pause(paused)

    @enqueue
    def next(self):
        if self.current_track_id == len(self.track_list) - 1:
            return

        self.current_track_id += 1
        self._notify_track_change()
        self._play_track()
        self._request_more_tracks()

    @enqueue
    def _request_more_tracks(self):
        if self.current_track_id == len(self.track_list) - 1:
            self.event_emitter.dispatch(EventType.RequestMoreTracks)

    @enqueue
    def prev(self):
        if self.current_track_id == 0:
            return

        self.current_track_id -= 1

        self._play_track()
        self._notify_track_change()

    @enqueue
    def stop(self):
        self.track_player.stop()

    @enqueue
    def pause(self, paused):
        self.track_player.pause(paused)

    @enqueue
    def seek(self, time_ms):
        pass
        # self.track_player.seek(time_ms)

    @enqueue
    def set_pos(self, pos):
        pass
        # self.track_player.set_pos(pos)

    @enqueue
    def add(self, tracks: list):
        index = len(self.track_list)
        self.track_list.extend(tracks)
        self.event_emitter.dispatch(
            EventType.TracksAdded,
            [self.get_track_info(i) for i in range(index, len(self.track_list))],
        )
        if self.current_track_id == index - 1 and index in self.prefetched_tracks:
            self.track_player.remove_context(self._prefetched_tracks[index])
            del self._prefetched_tracks[index]
            self._prepare_track(self.current_track_id + 1)

        if index == 0:
            self._notify_track_change()

    def _clean_prefetched(self):
        for context in self.prefetched_tracks.values():
            self.track_player.remove_context(context)

        self.prefetched_tracks = {}

    @enqueue
    def remove(self, tracks: list[int]):
        if self.current_track_id in tracks:
            self.track_player.stop()

        prev_track_id = self.current_track_id

        tracks.sort(reverse=True)
        for track in tracks:
            if track in self.prefetched_tracks:
                item = self.prefetched_tracks[track]
                self.track_player.remove_context(item["context"])
                del self.prefetched_tracks[track]

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
        return PlayerState(
            state=self.state.name,
            current_track=(
                self.get_track_info(self.current_track_id)
                if self.current_track_id in range(0, len(self.track_list))
                else None
            ),
            index=self.current_track_id,
            position=self._estimated_progress(),
        )

    def _estimated_progress(self) -> int:
        if self.state != State.PLAYING:
            return self.current_progress

        progress = self.current_progress + int(
            (time.monotonic_ns() - self.last_progress_update_ts) / 1_000_000
        )

        return progress

    @enqueue
    def replay(self) -> PlayerState:
        self.event_emitter.dispatch(
            EventType.StateReplay,
            PlayerState(
                state=self.state.name,
                current_track=(
                    self.get_track_info(self.current_track_id)
                    if self.current_track_id in range(0, len(self.track_list))
                    else None
                ),
                index=self.current_track_id,
                position=self._estimated_progress(),
            ).model_dump(exclude_none=True),
            self.list(0, len(self.track_list)),
        )

    def _clear(self):
        self.track_player.stop()
        self._clean_prefetched()
        list_len = len(self.track_list)
        self.track_list = []
        self.current_track_id = 0
        self.event_emitter.dispatch(
            EventType.TracksRemoved,
            [i for i in range(list_len - 1, -1, -1)],
        )

    @enqueue
    def clear(self):
        self._clear()

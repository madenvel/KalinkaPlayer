import logging
import time

from functools import partial
from typing import Optional
from pydantic import BaseModel
from threading import Timer

from data_model.response_model import AudioInfo, PlayerState
from data_model.datamodel import Track

from src.event_loop import AsyncExecutor, enqueue
from src.rpiasync import EventEmitter
from src.inputmodule import TrackInfo
from src.events import EventType

from native.rpiplayer import AudioPlayer, State, StateInfo


logger = logging.getLogger(__name__)


def to_audio_info(audio_info: AudioInfo):
    return (
        AudioInfo(
            sample_rate=audio_info.sample_rate,
            channels=audio_info.channels,
            bits_per_sample=audio_info.bits_per_sample,
            duration_ms=audio_info.duration_ms,
        )
        if audio_info
        else None
    )


def buffer_size(seconds: int, bits: int, sample_rate: int):
    bytes = bits / 8
    if bytes % 2 != 0:
        bytes += 1

    frame_size = bytes * 2
    frames_per_second = sample_rate * frame_size

    return int(frames_per_second * seconds)


class PlayQueueState(BaseModel):
    state_info: StateInfo
    current_track: Optional[Track] = None
    index: Optional[int] = None

    @staticmethod
    def empty():
        return PlayQueueState(state_info=StateInfo())

    class Config:
        arbitrary_types_allowed = True


class PlayQueue(AsyncExecutor):
    def __init__(self, event_emitter: EventEmitter):
        super().__init__()
        self.event_emitter = event_emitter
        self.track_player = AudioPlayer()
        self.track_player.set_state_callback(self._state_callback)

        self.current_track_id = 0
        self.current_context_id = -1
        self.track_list: list[TrackInfo] = []
        self.prefetched_tracks = {}
        self.context_state_map: dict[PlayQueueState] = {}
        self.timer_thread = None

    @enqueue
    def _state_callback(self, context_id, state_info):
        if state_info.state == State.STOPPED:
            self.context_state_map.pop(context_id, None)

        if context_id != self.current_context_id:
            return

        if state_info.state == State.FINISHED:
            if self.current_track_id == len(self.track_list) - 1:
                return self.stop()
            else:
                return self.next()

        if context_id not in self.context_state_map:
            self.context_state_map[context_id] = PlayQueueState.empty()
        context_state = self.context_state_map[context_id]

        new_audio_info = (
            to_audio_info(state_info.audio_info)
            if context_state.state_info.audio_info != state_info.audio_info
            else None
        )

        current_track = self.get_track_info(self.current_track_id)
        new_current_track = (
            current_track if context_state.current_track != current_track else None
        )
        new_index = (
            self.current_track_id
            if context_state.index != self.current_track_id
            else None
        )

        # Compensate delay between state state change and callback
        position_diff = 0
        if state_info.state == State.PLAYING:
            position_diff = int((time.monotonic_ns() - state_info.timestamp) / 1000)
            logger.info("Time drift: %d ms", position_diff)

        self.event_emitter.dispatch(
            EventType.StateChanged,
            PlayerState(
                state=state_info.state.name,
                current_track=new_current_track,
                index=new_index,
                position=state_info.position + position_diff,
                message=state_info.message,
                audio_info=new_audio_info,
            ).model_dump(exclude_none=True),
        )

        if state_info.state != State.STOPPED:
            context_state.state_info = state_info
            context_state.current_track = current_track
            context_state.index = self.current_track_id

        if state_info.state == State.PLAYING:
            self._setup_prefetch_timer()
        else:
            self._cancel_prefetch_timer()

    def _setup_prefetch_timer(self):
        self._cancel_prefetch_timer()
        next_track_id = self.current_track_id + 1
        state_info = self.context_state_map[self.current_context_id].state_info
        time_to_prefetch_s = (
            state_info.audio_info.duration_ms - state_info.position - 3000
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

    def _notify_track_change(self):
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

        if index is not None:
            self.current_track_id = index

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
        context_info = self.context_state_map.get(
            self.current_context_id, PlayQueueState.empty()
        )
        return PlayerState(
            state=context_info.state_info.state.name,
            current_track=(
                self.get_track_info(self.current_track_id)
                if self.current_track_id in range(0, len(self.track_list))
                else None
            ),
            index=self.current_track_id,
            position=self._estimated_progress(),
            message=context_info.state_info.message,
            audio_info=to_audio_info(context_info.state_info.audio_info),
        )

    def _estimated_progress(self) -> int:
        context_state = self.context_state_map.get(
            self.current_context_id, PlayQueueState.empty()
        )
        if context_state.state_info.state != State.PLAYING:
            return context_state.state_info.position

        progress = context_state.state_info.position + int(
            (time.monotonic_ns() - context_state.state_info.timestamp) / 1_000_000
        )

        return progress

    @enqueue
    def replay(self) -> PlayerState:
        context_info = self.context_state_map.get(
            self.current_context_id, PlayQueueState.empty()
        )
        self.event_emitter.dispatch(
            EventType.StateReplay,
            PlayerState(
                state=context_info.state_info.state.name,
                current_track=(
                    self.get_track_info(self.current_track_id)
                    if self.current_track_id in range(0, len(self.track_list))
                    else None
                ),
                index=self.current_track_id,
                position=self._estimated_progress(),
                message=context_info.state_info.message,
                audio_info=to_audio_info(context_info.state_info.audio_info),
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

import logging
import time
from collections import OrderedDict
from threading import Thread, Timer
from typing import Optional

from .async_common import EventEmitter
from .config_model import KalinkaConfig
from .event_loop import AsyncExecutor, enqueue

from kalinka_plugin_sdk.datamodel import (
    PlayerStateEnum,
    Track,
    AudioInfo,
    PlayerState,
    PlaybackMode,
    TrackList,
)
from kalinka_plugin_sdk.events import (
    NetworkErrorEvent,
    PlaybackModeChangedEvent,
    RequestMoreTracksEvent,
    StateChangedEvent,
    StateReplayEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo

from native_player.native_player import (
    AudioFormat,
    AudioGraphNodeState,
    AudioPlayer,
    py_dict_to_config,
    StreamInfo,
    StreamState,
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


def get_duration_ms(stream_info: StreamInfo) -> int:
    return int(stream_info.stream_size / stream_info.format.sample_rate * 1000)


def to_audio_info(stream_info: StreamInfo):
    if stream_info is None:
        return None
    return AudioInfo(
        sample_rate=stream_info.format.sample_rate,
        channels=stream_info.format.channels,
        bits_per_sample=stream_info.format.bits_per_sample,
        duration_ms=get_duration_ms(stream_info),
    )


def mime_to_format(mime: str) -> AudioFormat:
    """Convert MIME type to AudioFormat enum value used by the native player"""
    logger.debug(f"Detected mime type: {mime}")

    # Handle standard MIME types
    if mime:
        mime_lower = mime.lower()
        if mime_lower == "audio/flac" or mime_lower == "application/x-flac":
            return AudioFormat.FLAC
        elif mime_lower == "audio/mpeg" or mime_lower == "audio/mp3":
            return AudioFormat.MPEG
        # Fall back to substring check for non-standard MIME types
        elif "flac" in mime_lower:
            return AudioFormat.FLAC

    # Default to MPEG for all other formats
    return AudioFormat.MPEG


def to_state_name(state: AudioGraphNodeState) -> Optional[PlayerStateEnum]:
    if state == AudioGraphNodeState.ERROR:
        return PlayerStateEnum.ERROR
    elif state == AudioGraphNodeState.STOPPED or state == AudioGraphNodeState.FINISHED:
        return PlayerStateEnum.STOPPED
    elif state == AudioGraphNodeState.PREPARING:
        return PlayerStateEnum.BUFFERING
    elif state == AudioGraphNodeState.STREAMING:
        return PlayerStateEnum.PLAYING
    elif state == AudioGraphNodeState.PAUSED:
        return PlayerStateEnum.PAUSED


def flatten_dict(d, parent_key="", sep="."):
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_dict(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items


class PlayQueue(AsyncExecutor):
    def __init__(self, config: KalinkaConfig, event_emitter: EventEmitter):
        super().__init__()
        self.event_emitter = event_emitter
        self.config = py_dict_to_config(flatten_dict(config.model_dump()))
        self.track_player = AudioPlayer(self.config)
        self.current_track_id = 0
        self.current_format = None
        self.track_list: list[TrackInfo] = []

        # Playback mode
        self.shuffle = False  # TODO
        self.repeat_single = False
        self.repeat_all = False

        self.timer_thread = None
        self.state_monitor = self.track_player.monitor()
        self.prepared_tracks = OrderedDict()

        self.state_update_thread = Thread(target=self._state_update_listener)
        self.state_update_thread.start()

    def __del__(self):
        self.terminate()

    def terminate(self):
        self.track_player.stop()
        self.state_monitor.stop()
        self.state_update_thread.join()
        super().terminate()

    def _state_update_listener(self):
        while self.state_monitor.is_running():
            new_state = self.state_monitor.wait_state()
            logger.info(f"New state: {new_state}")
            self._process_state_update(new_state)

    @enqueue
    def _process_state_update(self, new_state):
        if new_state.state == AudioGraphNodeState.SOURCE_CHANGED:
            if self.prepared_tracks:
                item = self.prepared_tracks.popitem(last=False)
                self.current_track_id = item[0]
                self.current_format = item[1].format
                self._request_more_tracks()
            return
        elif new_state.state == AudioGraphNodeState.FINISHED:
            if self.timer_thread is None:
                self.play_next(self.current_track_id + 1)
        elif new_state.state == AudioGraphNodeState.STREAMING:
            self._setup_prefetch_timer(new_state)
        elif new_state.state != AudioGraphNodeState.STREAMING:
            self._cancel_prefetch_timer()

        state_update_ts = time.monotonic_ns()
        position_diff = 0
        if new_state.state == AudioGraphNodeState.STREAMING:
            position_diff = int((state_update_ts - new_state.timestamp) / 1_000_000)
        if new_state.state == AudioGraphNodeState.FINISHED:
            new_state.position = 0

        self.event_emitter.dispatch(
            StateChangedEvent(
                state=PlayerState(
                    state=to_state_name(new_state.state),
                    current_track=self.get_track_info(self.current_track_id),
                    index=self.current_track_id,
                    position=new_state.position + position_diff,
                    message=new_state.message,
                    audio_info=to_audio_info(new_state.stream_info),
                    mime_type=self.current_format,
                    timestamp=state_update_ts,
                )
            ),
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

        track_info = self._setup_track_to_play(index)
        if track_info is None:
            return

        self.prepared_tracks.clear()
        self.prepared_tracks[index] = track_info
        self.track_player.play(track_info.url, mime_to_format(track_info.format))

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

        track_info = self._setup_track_to_play(index)
        if track_info is None:
            return

        self.prepared_tracks[index] = track_info
        self.track_player.play_next(track_info.url, mime_to_format(track_info.format))

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
        if len(tracks) == 0:
            return

        index = len(self.track_list)
        self.track_list.extend(tracks)
        self.event_emitter.dispatch(
            TracksAddedEvent(
                tracks=[
                    track_info
                    for i in range(index, len(self.track_list))
                    if ((track_info := self.get_track_info(i)) is not None)
                ]
            ),
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
        self.event_emitter.dispatch(TracksRemovedEvent(indices=tracks))
        if prev_track_id != self.current_track_id or prev_track_id in tracks:
            self._notify_track_change()

    def list(self, offset: int, limit: int) -> TrackList:
        if offset not in range(0, len(self.track_list)):
            return TrackList(
                offset=offset,
                limit=limit,
                total=len(self.track_list),
                items=[],
            )

        return TrackList(
            offset=offset,
            limit=limit,
            total=len(self.track_list),
            items=[
                track_info
                for i in range(offset, min(offset + limit, len(self.track_list)))
                if ((track_info := self.get_track_info(i)) is not None)
            ],
        )

    def get_track_info(self, index: int) -> Optional[Track]:
        if index not in range(0, len(self.track_list)):
            return None
        track_info: TrackInfo = self.track_list[index]
        return track_info.metadata

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
            mime_type=self.current_format,
            timestamp=time.monotonic_ns(),
        )

    @enqueue
    def replay(self):
        stream_state = self.track_player.get_state()
        self.event_emitter.dispatch(
            StateReplayEvent(
                state=PlayerState(
                    state=to_state_name(stream_state.state),
                    current_track=self.get_track_info(self.current_track_id),
                    index=self.current_track_id,
                    position=self._estimated_progress(stream_state),
                    message=stream_state.message,
                    audio_info=to_audio_info(stream_state.stream_info),
                    mime_type=self.current_format,
                ),
                track_list=self.list(0, len(self.track_list)),
                playback_mode=PlaybackMode(
                    shuffle=self.shuffle,
                    repeat_single=self.repeat_single,
                    repeat_all=self.repeat_all,
                ),
            ),
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
            TracksRemovedEvent(indices=[i for i in range(list_len - 1, -1, -1)])
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
                logger.warn("Failed to retrieve track link:", repr(e))
                self.event_emitter.dispatch(
                    NetworkErrorEvent(message="Failed to retrieve track link")
                )
                return None

            return track_info

        return self.prepared_tracks[index]

    def _request_more_tracks(self):
        if not self.repeat_all and self.current_track_id == len(self.track_list) - 1:
            self.event_emitter.dispatch(RequestMoreTracksEvent())

    def _setup_prefetch_timer(self, state: StreamInfo):
        self._cancel_prefetch_timer()

        stream_info = state.stream_info
        if not stream_info:
            return

        time_to_prefetch_s = (
            get_duration_ms(stream_info) - state.position - PREFETCH_TIME_MS
        ) / 1000

        if time_to_prefetch_s <= 0:
            self._play_next_track_timer()
            return

        logger.info(f"Prefetching next track in {time_to_prefetch_s} seconds")

        self.timer_thread = Timer(
            time_to_prefetch_s,
            self._play_next_track_timer,
        )
        self.timer_thread.start()

    def _play_next_track_timer(self):
        next_track_id = self.current_track_id
        if not self.repeat_single:
            next_track_id += 1
        if self.repeat_all and next_track_id >= len(self.track_list):
            next_track_id = 0

        self.play_next(next_track_id)

    def _cancel_prefetch_timer(self):
        if self.timer_thread is not None:
            self.timer_thread.cancel()
            self.timer_thread = None

    def _notify_track_change(self):
        self.event_emitter.dispatch(
            StateChangedEvent(
                state=PlayerState(
                    current_track=self.get_track_info(self.current_track_id),
                    index=self.current_track_id,
                    state=to_state_name(AudioGraphNodeState.STOPPED),
                    position=0,
                    timestamp=time.monotonic_ns(),
                )
            ),
        )

    @enqueue
    def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ):
        repeat_single_updated = (
            self.repeat_single != repeat_single if repeat_single is not None else False
        )
        self.shuffle = shuffle if shuffle is not None else self.shuffle
        self.repeat_single = (
            repeat_single if repeat_single is not None else self.repeat_single
        )
        self.repeat_all = repeat_all if repeat_all is not None else self.repeat_all
        if (
            self.shuffle is not None
            or self.repeat_all is not None
            or self.repeat_single is not None
        ):
            self.event_emitter.dispatch(
                PlaybackModeChangedEvent(
                    mode=PlaybackMode(
                        shuffle=self.shuffle,
                        repeat_single=self.repeat_single,
                        repeat_all=self.repeat_all,
                    )
                ),
            )
            if repeat_single_updated:
                if self.prepared_tracks:
                    last_url = self.prepared_tracks.popitem(last=False)
                    self.track_player.remove(last_url[1])
                    self._play_next_track_timer()
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

    def get_playback_mode(self):
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

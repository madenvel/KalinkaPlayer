import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable
from typing import Callable, Optional

from .config_model import KalinkaConfig

from kalinka_queued import queued, queued_class

from kalinka_plugin_sdk.datamodel import (
    EntityId,
    PlayerStateEnum,
    Track,
    AudioInfo,
    PlaybackState,
    PlaybackMode,
    TrackList,
)
from kalinka_plugin_sdk.events import (
    PlayQueueState,
    PlaybackErrorEvent,
    PlaybackModeChangedEvent,
    RequestMoreTracksEvent,
    PlaybackStateChangedEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo

from kalinka_plugin_sdk.api import PlayQueueController, EventEmitter

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


class AsyncStateMonitor:
    """Asyncio-friendly wrapper around C++ StateMonitor.

    Provides async iterator protocol for consuming state changes without blocking
    the event loop.
    """

    def __init__(self, state_monitor):
        """Initialize with a C++ StateMonitor instance."""
        self._monitor = state_monitor

    async def wait_state(self):
        """Await for the next state change.

        Runs the blocking C++ waitState() call in a thread pool executor
        to avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, self._monitor.wait_state)
        return state

    def has_data(self) -> bool:
        """Check if there are queued state changes."""
        return self._monitor.has_data()

    def stop(self):
        """Stop monitoring state changes."""
        self._monitor.stop()

    def is_running(self) -> bool:
        """Check if the monitor is still running."""
        return self._monitor.is_running()

    def __aiter__(self):
        """Support async iteration protocol."""
        return self

    async def __anext__(self):
        """Get the next state change in async iteration.

        Returns:
            StreamState: The next state change

        Raises:
            StopAsyncIteration: When monitor stops
        """
        if not self._monitor.is_running():
            raise StopAsyncIteration
        return await self.wait_state()


@queued_class(timeout=10)
class PlayQueueImpl(PlayQueueController):
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

        self._prefetch_task = None
        self._state_monitor_raw = self.track_player.monitor()
        self.state_monitor = AsyncStateMonitor(self._state_monitor_raw)
        self.prepared_tracks = OrderedDict()

        self._state_update_task = None

    async def __aenter__(self):
        """Start the state update listener. Call this after initialization."""

        self._state_update_task = asyncio.create_task(
            self._state_update_listener_async()
        )

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Stop the play queue and cleanup resources."""
        self._terminate()
        if self._state_update_task:
            self._state_update_task.cancel()
            try:
                await self._state_update_task
            except asyncio.CancelledError:
                pass

    def _terminate(self):
        self.track_player.stop()
        self._state_monitor_raw.stop()

    async def _state_update_listener_async(self):
        """Listen for state changes using async iterator."""
        try:
            async for new_state in self.state_monitor:
                logger.info(f"New state: {new_state}")
                await self._process_state_update(new_state)
        except asyncio.CancelledError:
            logger.debug("State listener cancelled")
            raise

    @queued
    async def _process_state_update(self, new_state):
        if new_state.state == AudioGraphNodeState.SOURCE_CHANGED:
            if self.prepared_tracks:
                item = self.prepared_tracks.popitem(last=False)
                self.current_track_id = item[0]
                self.current_format = item[1].format
                self._request_more_tracks()
            return
        elif new_state.state == AudioGraphNodeState.FINISHED:
            if self._prefetch_task is None:
                self._play_next(self.current_track_id + 1)
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
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=to_state_name(new_state.state),
                    current_track=self._get_track_info(self.current_track_id),
                    index=self.current_track_id,
                    position=new_state.position + position_diff,
                    message=new_state.message,
                    audio_info=to_audio_info(new_state.stream_info),
                    mime_type=self.current_format,
                    timestamp=state_update_ts,
                )
            ),
        )

    async def play(self, index=None):
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

    async def play_next(self, index):
        self._play_next(index)

    def _play_next(self, index):
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

    async def pause(self, paused: bool):
        self.track_player.pause(paused)

    async def next(self):
        self._play_sync(self.current_track_id + 1)

    async def prev(self):
        self._play_sync(self.current_track_id - 1)

    async def seek(self, position_ms: int) -> None:
        return self.track_player.seek(position_ms)

    async def stop(self):
        self.track_player.stop()

    async def add(self, tracks: list[TrackInfo]):
        self._add(tracks)

    def _add(self, tracks: list[TrackInfo]):
        if len(tracks) == 0:
            return

        index = len(self.track_list)
        self.track_list.extend(tracks)
        self.event_emitter.dispatch(
            TracksAddedEvent(
                tracks=[
                    track_info
                    for i in range(index, len(self.track_list))
                    if ((track_info := self._get_track_info(i)) is not None)
                ]
            ),
        )

        if index == 0:
            self._notify_track_change()

    async def remove(self, tracks: list[int]):
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

    async def list(self, offset: int, limit: int) -> TrackList:
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
                if ((track_info := await self.get_track_info(i)) is not None)
            ],
        )

    async def get_track_info(self, index: int) -> Optional[Track]:
        return self._get_track_info(index)

    def _get_track_info(self, index: int) -> Optional[Track]:
        if index not in range(0, len(self.track_list)):
            return None
        track_info: TrackInfo = self.track_list[index]
        return track_info.metadata

    async def get_playback_state(self) -> PlaybackState:
        return self._get_playback_state()

    def _get_playback_state(self) -> PlaybackState:
        stream_state = self.track_player.get_state()
        return PlaybackState(
            state=to_state_name(stream_state.state),
            current_track=(
                self._get_track_info(self.current_track_id)
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

    async def restore_from_state(
        self,
        state: PlayQueueState,
        track_info_retriever: Callable[[EntityId], Awaitable[TrackInfo]],
    ) -> None:
        """Restore playqueue state from a PlayQueueState snapshot.

        Restores the playback mode, track list, and current track index.
        Playback state is set to STOPPED with position 0.

        Args:
            state: The PlayQueueState to restore from
            track_info_retriever: Async callback to retrieve TrackInfo from EntityId
        """
        # Stop any current playback
        self.track_player.stop()

        # Clear existing state
        self.track_list.clear()
        self.prepared_tracks.clear()
        self._cancel_prefetch_timer()

        # Restore track list
        if state.trackList:
            track_infos = []
            for track in state.trackList:
                try:
                    track_info = await track_info_retriever(track.id)
                    track_infos.append(track_info)
                except Exception as e:
                    logger.warning(f"Failed to retrieve track info for {track.id}: {e}")
                    continue

            if track_infos:
                self._add(track_infos)

        # Restore playback mode
        if state.playbackMode:
            self.shuffle = state.playbackMode.shuffle
            self.repeat_single = state.playbackMode.repeat_single
            self.repeat_all = state.playbackMode.repeat_all

            self.event_emitter.dispatch(
                PlaybackModeChangedEvent(mode=state.playbackMode)
            )

        # Restore current track index
        if state.playbackState and state.playbackState.index is not None:
            self.current_track_id = max(
                0, min(state.playbackState.index, len(self.track_list) - 1)
            )
        else:
            self.current_track_id = 0

        # Ensure current_track_id is valid
        if self.current_track_id < 0 or not self.track_list:
            self.current_track_id = 0

        # Emit a stopped state with position 0
        self.event_emitter.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    current_track=(
                        self._get_track_info(self.current_track_id)
                        if self.track_list
                        else None
                    ),
                    index=self.current_track_id,
                    state=PlayerStateEnum.STOPPED,
                    position=0,
                    timestamp=time.monotonic_ns(),
                )
            )
        )

        logger.info(
            f"Restored state: {len(self.track_list)} tracks, current_track_id={self.current_track_id}"
        )

    async def clear(self):
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
                logger.warning("Failed to retrieve track link: %s", repr(e))
                self.event_emitter.dispatch(
                    PlaybackErrorEvent(message="Failed to retrieve track link")
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
            self._prefetch_task = asyncio.create_task(self._play_next_track_async())
            return

        logger.info(f"Prefetching next track in {time_to_prefetch_s} seconds")

        self._prefetch_task = asyncio.create_task(
            self._prefetch_timer_async(time_to_prefetch_s)
        )

    async def _prefetch_timer_async(self, delay: float):
        try:
            await asyncio.sleep(delay)
            await self._play_next_track_async()
        except asyncio.CancelledError:
            logger.debug("Prefetch timer cancelled")
            raise

    async def _play_next_track_async(self):
        next_track_id = self.current_track_id
        if not self.repeat_single:
            next_track_id += 1
        if self.repeat_all and next_track_id >= len(self.track_list):
            next_track_id = 0

        await self.play_next(next_track_id)

    def _cancel_prefetch_timer(self):
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
            self._prefetch_task = None

    def _notify_track_change(self):
        self.event_emitter.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    current_track=self._get_track_info(self.current_track_id),
                    index=self.current_track_id,
                    state=to_state_name(AudioGraphNodeState.STOPPED),
                    position=0,
                    timestamp=time.monotonic_ns(),
                )
            ),
        )

    async def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ) -> PlaybackMode:
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
                    self._prefetch_task = asyncio.create_task(
                        self._play_next_track_async()
                    )
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

    async def get_playback_mode(self) -> PlaybackMode:
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

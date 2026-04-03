import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable
from typing import Callable, Optional

from kalinka_serialized.serial_executor import interrupt

from .config_model import KalinkaConfig

from kalinka_serialized import SerialExecutor, serialised, with_serial_executor

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
    TrackMovedEvent,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo

from kalinka_plugin_sdk.api import PlayQueueController, EventEmitter

from native_player.native_player import (
    AudioFormat,
    AudioGraphNodeState,
    AudioPlayer,
    py_dict_to_config,
    StreamErrorSource,
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


def _remap_index(idx: int, from_index: int, to_index: int) -> int:
    """Remap an index after moving a track from from_index to to_index.

    Mirrors the effect of: list.pop(from_index); list.insert(to_index, item).
    """
    if idx == from_index:
        return to_index
    if from_index < to_index:  # track moved forward
        if from_index < idx <= to_index:
            return idx - 1
    else:  # track moved backward
        if to_index <= idx < from_index:
            return idx + 1
    return idx


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
    return None


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


# State restore can involve many I/O calls; keep it outside the serial executor
# so startup restore does not block the command lane.
@with_serial_executor
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
        self.prepared_tracks: OrderedDict = OrderedDict()
        # StreamId of the stream currently being played (popped from prepared_tracks on SOURCE_CHANGED)
        self.current_stream_id: Optional[int] = None
        self._retry_attempted: bool = False
        # Set to True when SOURCE_CHANGED is expected from a retry, to avoid resetting _retry_attempted
        self._retry_pending: bool = False

        self._state_update_task = None

    async def __aenter__(self):
        """Start the state update listener. Call this after initialization."""

        self._state_update_task = asyncio.create_task(
            self._state_update_listener_async()
        )
        return self

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
                try:
                    await self._process_state_update(new_state)
                except TimeoutError as e:
                    logger.error(
                        f"State update processing timed out: {e}. "
                        "Continuing to listen for state changes."
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing state update: {e}. "
                        "Continuing to listen for state changes.",
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.debug("State listener cancelled")
            raise

    @interrupt
    async def _process_state_update(self, new_state):
        if new_state.state == AudioGraphNodeState.SOURCE_CHANGED:
            if self._retry_pending:
                # SOURCE_CHANGED caused by a retry — keep _retry_attempted=True so we don't retry again
                self._retry_pending = False
            else:
                self._retry_attempted = False
            if self.prepared_tracks:
                item = self.prepared_tracks.popitem(last=False)
                self.current_track_id = item[0]
                track_url, stream_id = item[1]
                self.current_format = track_url.format
                self.current_stream_id = stream_id
                self._request_more_tracks()
            return
        elif new_state.state == AudioGraphNodeState.ERROR:
            if (
                not self._retry_attempted
                and new_state.error is not None
                and new_state.error.source == StreamErrorSource.HTTP_STREAM
            ):
                self._retry_attempted = True
                failed_position_ms = new_state.position
                logger.info(
                    "HTTP stream error at %d ms, retrying with fresh URL",
                    failed_position_ms,
                )
                await self._retry_current_track_async(failed_position_ms)
                return
            self._cancel_prefetch_timer()
        elif new_state.state == AudioGraphNodeState.FINISHED:
            # Only auto-play next if no streams are already queued.
            # Non-empty prepared_tracks means a manual switch already appended
            # a new stream — SOURCE_CHANGED will handle the transition.
            if self._prefetch_task is None and not self.prepared_tracks:
                await self._play_unqueued(self.current_track_id + 1)
        elif new_state.state == AudioGraphNodeState.STOPPED:
            # The prefetch may have completed and appended a stream just as the
            # native player ran out of time and stopped.  If a stream is already
            # queued and the player is still stopped (i.e. play_next hasn't
            # restarted it yet), do a full _play_unqueued so the remove+append
            # cycle kicks the player back into motion.
            if (
                self.prepared_tracks
                and self.track_player.get_state().state == AudioGraphNodeState.STOPPED
            ):
                next_idx = next(iter(self.prepared_tracks))
                await self._play_unqueued(next_idx)
                return  # new playback started; its own state events will follow
            self._cancel_prefetch_timer()
        elif new_state.state == AudioGraphNodeState.STREAMING:
            self._setup_prefetch_timer(new_state)
        else:
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
                    message=new_state.error.message if new_state.error else None,
                    audio_info=to_audio_info(new_state.stream_info),
                    mime_type=self.current_format,
                    timestamp_ns=state_update_ts,
                )
            ),
        )

    @serialised
    async def play(self, index: Optional[int] = None) -> None:
        await self._play_unqueued(index)

    @serialised
    async def play_next(self, index: int) -> None:
        return await self._play_next_unqueued(index)

    async def _play_unqueued(self, index=None):
        self._retry_attempted = False
        if len(self.track_list) == 0:
            return

        if index is not None and index not in range(0, len(self.track_list)):
            return

        if index is None:
            index = self.current_track_id

        track_info = await self._setup_track_to_play(index)
        if track_info is None:
            return

        self._cancel_prefetch_timer()

        # Remove any prefetched streams.
        for _, (_, stream_id) in list(self.prepared_tracks.items()):
            self.track_player.remove(stream_id)
        self.prepared_tracks.clear()

        # Append new stream — auto-starts. prepared_tracks is non-empty so the
        # FINISHED handler (triggered by the removals above) will not auto-play.
        stream_id = self.track_player.append(
            track_info.url, mime_to_format(track_info.format)
        )
        self.prepared_tracks[index] = (track_info, stream_id)

        if self.current_stream_id is not None:
            self.track_player.remove(self.current_stream_id)
            self.current_stream_id = None

    async def _play_next_unqueued(self, index):
        if len(self.track_list) == 0:
            return

        if index not in range(0, len(self.track_list)) or index in self.prepared_tracks:
            return

        logger.info(f"Playing next track index={index}")

        track_info = await self._setup_track_to_play(index)
        if track_info is None:
            return

        stream_id = self.track_player.append(
            track_info.url, mime_to_format(track_info.format)
        )
        self.prepared_tracks[index] = (track_info, stream_id)

        # If the player stopped before the prefetch completed (slow URL fetch),
        # fall back to a full _play_unqueued so the remove+append cycle restarts it.
        if self.track_player.get_state().state == AudioGraphNodeState.STOPPED:
            await self._play_unqueued(index)

    @serialised
    async def pause(self, paused: bool):
        if paused:
            self.track_player.pause()
        else:
            self.track_player.resume()

    @serialised
    async def next(self):
        await self._play_unqueued(self.current_track_id + 1)

    @serialised
    async def prev(self):
        await self._play_unqueued(self.current_track_id - 1)

    @serialised
    async def seek(self, position_ms: int) -> None:
        return self.track_player.seek(position_ms)

    @serialised
    async def stop(self):
        self.track_player.stop()

    @serialised
    async def add(self, tracks: list[TrackInfo], index: Optional[int] = None):
        self._add(tracks, index)

    def _add(self, tracks: list[TrackInfo], index: Optional[int] = None):
        if len(tracks) == 0:
            return

        # Resolve insertion position (default: append)
        insert_at = len(self.track_list) if index is None else index
        # Clamp to valid range
        insert_at = max(0, min(insert_at, len(self.track_list)))

        was_empty = len(self.track_list) == 0
        old_current_track_id = self.current_track_id

        # Insert tracks into the list
        for i, track in enumerate(tracks):
            self.track_list.insert(insert_at + i, track)

        # Shift current_track_id if insertion is before or at it (and queue wasn't empty)
        if not was_empty and insert_at <= self.current_track_id:
            self.current_track_id += len(tracks)

        # Shift all prepared_tracks keys >= insert_at up by len(tracks)
        new_prepared = OrderedDict()
        for idx, stream_info in self.prepared_tracks.items():
            new_prepared[idx + len(tracks) if idx >= insert_at else idx] = stream_info
        self.prepared_tracks = new_prepared

        # Validate prefetched next track (same pattern as move())
        expected_next = self.current_track_id
        if not self.repeat_single:
            expected_next += 1
        if self.repeat_all and expected_next >= len(self.track_list):
            expected_next = 0

        for idx in list(self.prepared_tracks.keys()):
            if idx == self.current_track_id:
                continue
            if idx != expected_next:
                _, stream_id = self.prepared_tracks.pop(idx)
                self.track_player.remove(stream_id)
                self._cancel_prefetch_timer()
                self._prefetch_task = asyncio.create_task(self._play_next_track_async())

        self.event_emitter.dispatch(
            TracksAddedEvent(
                tracks=[
                    track_info
                    for i in range(insert_at, insert_at + len(tracks))
                    if ((track_info := self._get_track_info(i)) is not None)
                ],
                index=insert_at,
            ),
        )

        if was_empty:
            self._notify_track_change()
        elif self.current_track_id != old_current_track_id:
            self.event_emitter.dispatch(
                PlaybackStateChangedEvent(state=self._get_playback_state())
            )

    @serialised
    async def remove(self, tracks: list[int]):
        prev_track_id = self.current_track_id

        tracks.sort(reverse=True)
        for track in tracks:
            # Remove native stream for the current track
            if track == self.current_track_id and self.current_stream_id is not None:
                self.track_player.remove(self.current_stream_id)
                self.current_stream_id = None
            # Remove native stream for prefetched tracks
            if track in self.prepared_tracks:
                _, stream_id = self.prepared_tracks[track]
                self.track_player.remove(stream_id)
                del self.prepared_tracks[track]

            if track < self.current_track_id:
                self.current_track_id -= 1

            del self.track_list[track]

        # Re-map prepared_tracks keys: each key shifts down by the number of
        # removed indices that were below it.
        new_prepared = OrderedDict()
        for key, value in self.prepared_tracks.items():
            shift = sum(1 for t in tracks if t < key)
            new_prepared[key - shift] = value
        self.prepared_tracks = new_prepared

        self.current_track_id = min(self.current_track_id, len(self.track_list) - 1)
        if self.current_track_id < 0:
            self.current_track_id = 0
        self.event_emitter.dispatch(TracksRemovedEvent(indices=tracks))
        if prev_track_id != self.current_track_id or prev_track_id in tracks:
            self._notify_track_change()

    @serialised
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
                if ((track_info := self._get_track_info(i)) is not None)
            ],
        )

    @serialised
    async def get_track_info(self, index: int) -> Optional[Track]:
        return self._get_track_info(index)

    def _get_track_info(self, index: int) -> Optional[Track]:
        if index not in range(0, len(self.track_list)):
            return None
        track_info: TrackInfo = self.track_list[index]
        return track_info.metadata

    @serialised
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
            message=stream_state.error.message if stream_state.error else None,
            audio_info=to_audio_info(stream_state.stream_info),
            mime_type=self.current_format,
            timestamp_ns=time.monotonic_ns(),
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
        was_already_stopped = (
            self.track_player.get_state().state == AudioGraphNodeState.STOPPED
        )
        self.track_player.stop()
        self.current_stream_id = None

        # Clear existing state
        self.track_list.clear()
        self.prepared_tracks.clear()
        self._cancel_prefetch_timer()

        # Pre-restore current_track_id so that _notify_track_change (fired by
        # _add when the queue was empty) emits the correct index straight away,
        # avoiding a spurious event with index=0 followed by the real index.
        target_index = 0
        if state.playback_state and state.playback_state.index is not None:
            target_index = state.playback_state.index
        self.current_track_id = target_index

        # Restore track list
        if state.track_list:
            track_infos = []
            for track in state.track_list:
                try:
                    track_info = await track_info_retriever(track.id)
                    track_infos.append(track_info)
                except Exception as e:
                    logger.warning(f"Failed to retrieve track info for {track.id}: {e}")
                    continue

            if track_infos:
                self._add(track_infos)

        # Clamp to valid range now that track_list is populated
        if self.track_list:
            self.current_track_id = max(
                0, min(self.current_track_id, len(self.track_list) - 1)
            )
        else:
            self.current_track_id = 0

        # Restore playback mode
        if state.playback_mode:
            self.shuffle = state.playback_mode.shuffle
            self.repeat_single = state.playback_mode.repeat_single
            self.repeat_all = state.playback_mode.repeat_all

            self.event_emitter.dispatch(
                PlaybackModeChangedEvent(mode=state.playback_mode)
            )

        # If the player was already stopped before restore, _add's
        # _notify_track_change won't fire (no tracks were loaded), so we need
        # to emit a state event ourselves.
        if was_already_stopped and not self.track_list:
            self.event_emitter.dispatch(
                PlaybackStateChangedEvent(
                    state=PlaybackState(
                        current_track=None,
                        index=0,
                        state=PlayerStateEnum.STOPPED,
                        position=0,
                        timestamp_ns=time.monotonic_ns(),
                    )
                )
            )

        logger.info(
            f"Restored state: {len(self.track_list)} tracks, current_track_id={self.current_track_id}"
        )

    @serialised
    async def move(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        if from_index not in range(0, len(self.track_list)):
            return
        if to_index not in range(0, len(self.track_list)):
            return

        # Reorder the track list
        track = self.track_list.pop(from_index)
        self.track_list.insert(to_index, track)

        # Remap current_track_id to follow its track
        old_current_track_id = self.current_track_id
        self.current_track_id = _remap_index(
            self.current_track_id, from_index, to_index
        )

        # Remap all prepared_tracks keys to follow their tracks
        new_prepared = OrderedDict()
        for idx, stream_info in self.prepared_tracks.items():
            new_prepared[_remap_index(idx, from_index, to_index)] = stream_info
        self.prepared_tracks = new_prepared

        # Verify the prefetched "next" track is still the correct one.
        # If the wrong track is queued as next, remove it and re-prefetch.
        expected_next = self.current_track_id
        if not self.repeat_single:
            expected_next += 1
        if self.repeat_all and expected_next >= len(self.track_list):
            expected_next = 0

        for idx in list(self.prepared_tracks.keys()):
            if idx == self.current_track_id:
                continue
            if idx != expected_next:
                _, stream_id = self.prepared_tracks.pop(idx)
                self.track_player.remove(stream_id)
                self._cancel_prefetch_timer()
                self._prefetch_task = asyncio.create_task(self._play_next_track_async())

        self.event_emitter.dispatch(
            TrackMovedEvent(from_index=from_index, to_index=to_index)
        )
        if self.current_track_id != old_current_track_id:
            self.event_emitter.dispatch(
                PlaybackStateChangedEvent(state=self._get_playback_state())
            )

    @serialised
    async def clear(self):
        self._clear()

    def _clear(self):
        was_already_stopped = (
            self.track_player.get_state().state == AudioGraphNodeState.STOPPED
        )
        self.track_player.clear_all()
        self.current_stream_id = None
        self.prepared_tracks.clear()
        list_len = len(self.track_list)
        self.track_list = []
        self.current_track_id = 0
        self.event_emitter.dispatch(
            TracksRemovedEvent(indices=[i for i in range(list_len - 1, -1, -1)])
        )
        if was_already_stopped:
            self.event_emitter.dispatch(
                PlaybackStateChangedEvent(
                    state=PlaybackState(
                        state=PlayerStateEnum.STOPPED,
                        current_track=self._get_track_info(self.current_track_id),
                        index=self.current_track_id,
                        position=0,
                        timestamp_ns=time.monotonic_ns(),
                    )
                )
            )

    def _estimated_progress(self, stream_state: StreamState) -> int:
        if stream_state.state != AudioGraphNodeState.STREAMING:
            return stream_state.position

        progress = stream_state.position + int(
            (time.monotonic_ns() - stream_state.timestamp) / 1_000_000
        )

        return progress

    async def _setup_track_to_play(self, index):
        """Returns a TrackUrl for the given track index, fetching if not already prepared."""
        track = self.track_list[index]
        if index not in self.prepared_tracks:
            try:
                track_info = await track.link_retriever()
            except Exception as e:
                logger.warning("Failed to retrieve track link: %s", repr(e))
                self.event_emitter.dispatch(
                    PlaybackErrorEvent(message="Failed to retrieve track link")
                )
                return None

            return track_info

        # Return just the TrackUrl part of the (TrackUrl, StreamId) tuple
        return self.prepared_tracks[index][0]

    async def _retry_current_track_async(self, position_ms: int) -> None:
        """Re-fetch the URL for the current track and resume from position_ms."""
        track_index = self.current_track_id
        track = self.track_list[track_index]

        try:
            track_info = await track.link_retriever()
        except Exception as e:
            logger.warning("Retry: failed to retrieve track link: %s", repr(e))
            self.event_emitter.dispatch(
                PlaybackErrorEvent(message="Failed to retrieve track link on retry")
            )
            return

        self._cancel_prefetch_timer()

        if self.current_stream_id is not None:
            self.track_player.remove(self.current_stream_id)
            self.current_stream_id = None

        for _, (_, stream_id) in list(self.prepared_tracks.items()):
            self.track_player.remove(stream_id)
        self.prepared_tracks.clear()

        self._retry_pending = True
        stream_id = self.track_player.append(
            track_info.url, mime_to_format(track_info.format)
        )
        self.prepared_tracks[track_index] = (track_info, stream_id)

        if position_ms > 0:
            self.track_player.seek(position_ms)

    def _request_more_tracks(self):
        if not self.repeat_all and self.current_track_id == len(self.track_list) - 1:
            self.event_emitter.dispatch(RequestMoreTracksEvent())

    def _setup_prefetch_timer(self, state: StreamState):
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
                    timestamp_ns=time.monotonic_ns(),
                )
            ),
        )

    @serialised
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
            shuffle is not None
            or repeat_all is not None
            or repeat_single is not None
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
                    _, (_, stream_id) = self.prepared_tracks.popitem(last=False)
                    self.track_player.remove(stream_id)
                    self._prefetch_task = asyncio.create_task(
                        self._play_next_track_async()
                    )
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

    @serialised
    async def get_playback_mode(self) -> PlaybackMode:
        return PlaybackMode(
            shuffle=self.shuffle,
            repeat_single=self.repeat_single,
            repeat_all=self.repeat_all,
        )

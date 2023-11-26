from src.event_loop import AsyncExecutor, enqueue

from src.rpiasync import EventEmitter
from enum import Enum
from src.track_url_retriever import TrackUrlRetriever

from src.trackbrowser import TrackInfo
from native.rpiplayer import RpiAudioPlayer


def buffer_size(seconds: int, bits: int, sample_rate: int):
    bytes = bits / 8
    if bytes % 2 != 0:
        bytes += 1

    frame_size = bytes * 2
    frames_per_second = sample_rate * frame_size

    return frames_per_second * seconds


class State(Enum):
    IDLE = 0
    READY = 1
    BUFFERING = 2
    PLAYING = 3
    PAUSED = 4
    FINISHED = 5
    STOPPED = 6
    ERROR = 7


class EventType(Enum):
    Playing = "playing"
    Paused = "paused"
    Stopped = "stopped"
    Progress = "current_progress"
    TrackChanged = "change_track"
    RequestMoreTracks = "request_more_tracks"
    TracksAdded = "track_added"
    TracksRemoved = "track_removed"


class PlayQueue(AsyncExecutor):
    def __init__(self, event_emitter: EventEmitter):
        super().__init__()
        self.event_emitter = event_emitter
        self.track_player = RpiAudioPlayer()
        self.track_player.set_progress_callback(self._progress_callback)
        self.track_player.set_state_callback(self._state_callback)

        self.current_track_id = 0
        self.current_context_id = -1
        self.context_map = {}
        self.track_list: list[TrackInfo] = []
        self.prefetched_tracks = {}

        # properties
        self.advance = True
        self.state = State.IDLE

    @enqueue
    def _state_callback(self, context_id, old_state, new_state):
        print("Context", context_id, "state", State(new_state).name)
        if context_id != self.current_context_id:
            return

        new_state = State(new_state)

        if new_state == State.FINISHED:
            return self._finished_playing()

        stateMap = {
            State.PLAYING: EventType.Playing,
            State.PAUSED: EventType.Paused,
            State.STOPPED: EventType.Stopped,
        }
        if new_state in stateMap:
            self.event_emitter.dispatch(stateMap[new_state])

    @enqueue
    def _progress_callback(self, progress):
        current_time = self.get_track_info(self.current_track_id)["duration"] * progress
        self.event_emitter.dispatch(
            EventType.Progress,
            current_time,
        )
        remaining = (1 - progress) * self.get_track_info(self.current_track_id)[
            "duration"
        ]
        if remaining < 5:
            self._prepare_track(self.current_track_id + 1)

    @enqueue
    def _finished_playing(self):
        if self.advance is True:
            self.next()

    def _notify_track_change(self):
        track = self.track_list[self.current_track_id]
        self.event_emitter.dispatch(
            EventType.TrackChanged, self.get_track_info(self.current_track_id)
        )

    @enqueue
    def _prepare_track(self, index):
        if index not in self.prefetched_tracks and index < len(self.track_list):
            link_retriever = self.track_list[index].link_retriever
            track_id = self.track_list[index].metadata["id"]
            track_info = link_retriever()
            if track_id == self.track_list[index].metadata["id"]:
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
            track_info = track.link_retriever()
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

        self._notify_track_change()
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
    def add(self, tracks: list, replace=False):
        if replace is True:
            self._clean_prefetched()
            self.event_emitter.dispatch(
                EventType.TracksRemoved, range(len(self.track_list))
            )
            self.track_list = []

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

    def _clean_prefetched(self):
        for context in self.prefetched_tracks.values():
            self.track_player.remove_context(context)

        self.prefetched_tracks = {}

    @enqueue
    def remove(self, tracks: list[int]):
        for track in tracks:
            if track in self.prefetched_tracks:
                item = self.prefetched_tracks[track]
                self.track_player.remove_context(item["context"])
                del self.prefetched_tracks[track]

            del self.track_list[track]

        self.event_emitter.dispatch(EventType.TracksRemoved, tracks)

    def list(self, offset: int, limit: int):
        if offset not in range(0, len(self.track_list)):
            return []

        return [
            self.get_track_info(i)
            for i in range(offset, min(offset + limit, len(self.track_list)))
        ]

    def get_track_info(self, index: int):
        track_info: TrackInfo = self.track_list[index]
        return {
            "index": index,
            "selected": index == self.current_track_id,
        } | track_info.metadata.dict()

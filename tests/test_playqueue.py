import time
import pytest
from unittest.mock import Mock, call

from data_model.response_model import AudioInfo, PlayerState
from src.events import EventType
from src.inputmodule import TrackInfo, Track, TrackUrl
from data_model.datamodel import Album
from src.playqueue import PlayQueue, AudioGraphNodeState, StreamState
from src.rpiasync import EventEmitter


def create_track(id: str):
    return Track(
        id=id, title="track" + id, duration=10, album=Album(id="1", title="album1")
    )


def url1():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-3.flac",
        format="FLAC",
        sample_rate=1,
        bit_depth=1,
    )


def url2():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-4.flac",
        format="FLAC",
        sample_rate=1,
        bit_depth=1,
    )


def url3():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-2.flac",
        format="FLAC",
        sample_rate=1,
        bit_depth=1,
    )


@pytest.fixture
def event_emitter():
    yield Mock(spec=EventEmitter)


@pytest.fixture
def playqueue(event_emitter):
    pq = PlayQueue(event_emitter)

    yield pq

    pq.terminate()

    del pq


def test_add_remove_track(event_emitter, playqueue):
    track = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    playqueue.add([track])
    playqueue.remove([0])
    time.sleep(1)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.TracksAdded, [track.metadata.model_dump(exclude_unset=True)]
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(EventType.TracksRemoved, [0]),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ).model_dump(exclude_none=True),
        ),
    ]
    event_emitter.assert_has_calls(expected_calls)


def test_play(event_emitter, playqueue):
    track = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    playqueue.add([track])
    playqueue.play()
    time.sleep(4)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.TracksAdded, [track.metadata.model_dump(exclude_unset=True)]
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(EventType.RequestMoreTracks),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PLAYING",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=13839
                ),
            ).model_dump(exclude_none=True),
        ),
    ]
    event_emitter.assert_has_calls(expected_calls)


def test_switch_track(event_emitter, playqueue):
    track1 = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    track2 = TrackInfo(id="2", metadata=create_track("2"), link_retriever=url2)
    playqueue.add([track1, track2])
    playqueue.play(0)
    time.sleep(4)
    playqueue.play(1)
    time.sleep(4)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.TracksAdded,
            [
                track1.metadata.model_dump(exclude_unset=True),
                track2.metadata.model_dump(exclude_unset=True),
            ],
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PLAYING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=13839
                ),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PLAYING",
                index=1,
                position=0,
                current_track=track2.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=14814
                ),
            ).model_dump(exclude_none=True),
        ),
    ]
    event_emitter.assert_has_calls(expected_calls)


# call play, then play_next and then play(1) after a second
def test_play_next(event_emitter, playqueue):
    track1 = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    track2 = TrackInfo(id="2", metadata=create_track("2"), link_retriever=url2)
    track3 = TrackInfo(id="3", metadata=create_track("3"), link_retriever=url3)
    playqueue.add([track1, track2, track3])
    playqueue.play(0)
    time.sleep(4)
    playqueue.play_next(1)
    time.sleep(2)
    playqueue.play(2)
    time.sleep(4)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.TracksAdded,
            [
                track1.metadata.model_dump(exclude_unset=True),
                track2.metadata.model_dump(exclude_unset=True),
                track3.metadata.model_dump(exclude_unset=True),
            ],
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PLAYING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=13839
                ),
            ).model_dump(exclude_none=True),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PLAYING",
                index=2,
                position=0,
                current_track=track3.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=90632
                ),
            ).model_dump(exclude_none=True),
        ),
    ]
    event_emitter.assert_has_calls(expected_calls)

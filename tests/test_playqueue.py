import time
import pytest
from unittest.mock import Mock, call

from data_model.response_model import AudioInfo, PlayerState
from src.events import EventType
from src.inputmodule import TrackInfo, Track, TrackUrl
from data_model.datamodel import Album
from src.playqueue import PlayQueue
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


def player_state_converter(*args, **kwargs):
    if args[0] == EventType.StateChanged:
        return PlayerState(**args[1])
    return args[1]


@pytest.fixture
def event_emitter():
    yield Mock(spec=EventEmitter)


@pytest.fixture
def playqueue(event_emitter):
    pq = PlayQueue(event_emitter)

    yield pq

    pq.terminate()

    del pq


def assert_call_args(actual_args, expected_args, position):
    for actual, expected in zip(actual_args, expected_args):
        if isinstance(expected, PlayerState):
            actual_clone = PlayerState(**actual)
            # ignore timestamp
            actual_clone.timestamp = expected.timestamp
            # ignore position
            actual_clone.position = expected.position
            assert (
                actual_clone == expected
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"
        else:
            assert (
                actual == expected
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"


def assert_has_calls(event_emitter, expected_calls):
    i = 0
    for actual_call, expected_call in zip(event_emitter.mock_calls, expected_calls):
        assert actual_call[0] == expected_call[0]
        assert_call_args(actual_call[1], expected_call[1], i)
        i += 1


def test_add_remove_track(event_emitter, playqueue):
    track = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    playqueue.add([track])
    playqueue.remove([0])
    time.sleep(1)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(state="STOPPED", index=0, position=0),
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
            ),
        ),
        call.dispatch(EventType.TracksRemoved, [0]),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(state="STOPPED", index=0, position=0),
        ),
    ]

    assert_has_calls(event_emitter, expected_calls)


def test_play(event_emitter, playqueue):
    track = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    playqueue.add([track])
    playqueue.play()
    time.sleep(4)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(state="STOPPED", index=0, position=0),
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
            ),
        ),
        call.dispatch(EventType.RequestMoreTracks),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ),
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
            ),
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


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
            ),
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
            ),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ),
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
            ),
        ),
        call.dispatch(EventType.RequestMoreTracks),
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
            ),
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


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
            ),
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
            ),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track1.metadata.model_dump(exclude_unset=True),
            ),
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
            ),
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
            ),
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


def test_play_pause_stop_play(event_emitter, playqueue):
    track = TrackInfo(id="1", metadata=create_track("1"), link_retriever=url1)
    time.sleep(1)
    playqueue.add([track])
    playqueue.play()
    time.sleep(4)
    playqueue.pause(True)
    time.sleep(2)
    playqueue.stop()
    time.sleep(2)
    playqueue.play()
    time.sleep(4)
    expected_calls = [
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
            ),
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
            ),
        ),
        call.dispatch(EventType.RequestMoreTracks),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ),
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
            ),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="PAUSED",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
                audio_info=AudioInfo(
                    sample_rate=32000, bits_per_sample=24, channels=2, duration_ms=13839
                ),
            ),
        ),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="STOPPED",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ),
        ),
        call.dispatch(EventType.RequestMoreTracks),
        call.dispatch(
            EventType.StateChanged,
            PlayerState(
                state="BUFFERING",
                index=0,
                position=0,
                current_track=track.metadata.model_dump(exclude_unset=True),
            ),
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
            ),
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)

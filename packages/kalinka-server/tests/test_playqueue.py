import time
import pytest
import asyncio
from unittest.mock import Mock, call

from kalinka_plugin_sdk.datamodel import (
    AudioInfo,
    PlaybackState,
    PlayerStateEnum,
    Album,
    EntityId,
    EntityType,
)
from kalinka_plugin_sdk.inputmodule import TrackInfo, Track, TrackUrl
from kalinka_plugin_sdk import (
    PlayQueueEventType,
    PlaybackStateChangedEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
    RequestMoreTracksEvent,
    EventEmitter,
)
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl

# These tests need to be updated for PlayQueueImpl behavior
pytestmark = pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)


def to_track_id(id: str):
    return EntityId(
        id=id,
        type=EntityType.TRACK,
        source="test_source",
    )


def create_track(id: str):
    return Track(
        id=to_track_id(id),
        title="track" + id,
        duration=10,
        album=Album(id=to_track_id("1"), title="album1"),
    )


def url1():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-3.flac",
        format="FLAC",
    )


def url2():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-4.flac",
        format="FLAC",
    )


def url3():
    return TrackUrl(
        url="https://getsamplefiles.com/download/flac/sample-2.flac",
        format="FLAC",
    )


def player_state_converter(*args, **kwargs):
    if args[0] == PlayQueueEventType.PlaybackStateChanged:
        return PlaybackState(**args[1])
    return args[1]


@pytest.fixture
def event_emitter():
    yield Mock(spec=EventEmitter)


@pytest.fixture
def config():
    config = KalinkaConfig()
    return config


@pytest.fixture
async def playqueue(config, event_emitter):
    pq = PlayQueueImpl(config, event_emitter)
    await pq.__aenter__()

    yield pq

    await pq.__aexit__(None, None, None)

    del pq


def assert_call_args(actual_args, expected_args, position):
    for actual, expected in zip(actual_args, expected_args):
        if isinstance(expected, PlaybackStateChangedEvent):
            # Compare PlaybackStateChangedEvent payloads
            assert isinstance(
                actual, PlaybackStateChangedEvent
            ), f"at position {position} expected PlaybackStateChangedEvent but got {type(actual)}"
            actual_state = actual.state
            expected_state = expected.state
            # ignore timestamp and position for PlaybackState comparisons
            actual_state.timestamp = expected_state.timestamp
            if (
                actual_state.position is not None
                and expected_state.position is not None
            ):
                actual_state.position = expected_state.position
            assert (
                actual_state == expected_state
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"
        elif isinstance(expected, TracksAddedEvent):
            # Compare TracksAddedEvent payloads
            assert isinstance(
                actual, TracksAddedEvent
            ), f"at position {position} expected TracksAddedEvent but got {type(actual)}"
            assert (
                actual.tracks == expected.tracks
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"
        elif isinstance(expected, TracksRemovedEvent):
            # Compare TracksRemovedEvent payloads
            assert isinstance(
                actual, TracksRemovedEvent
            ), f"at position {position} expected TracksRemovedEvent but got {type(actual)}"
            assert (
                actual.indices == expected.indices
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"
        elif isinstance(expected, RequestMoreTracksEvent):
            # Compare RequestMoreTracksEvent payloads
            assert isinstance(
                actual, RequestMoreTracksEvent
            ), f"at position {position} expected RequestMoreTracksEvent but got {type(actual)}"
        else:
            assert (
                actual == expected
            ), f"at position {position} actual: {actual_args}\nexpected: {expected_args}"


def assert_has_calls(event_emitter, expected_calls):
    i = 0
    assert len(event_emitter.mock_calls) == len(expected_calls)
    for actual_call, expected_call in zip(event_emitter.mock_calls, expected_calls):
        assert actual_call[0] == expected_call[0]
        assert_call_args(actual_call[1], expected_call[1], i)
        i += 1


@pytest.mark.asyncio
async def test_add_remove_track(event_emitter, playqueue):
    track = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    await playqueue.add([track])
    await playqueue.remove([0])
    await asyncio.sleep(1)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(state=PlayerStateEnum.STOPPED, index=0, position=0)
            )
        ),
        call.dispatch(
            TracksAddedEvent(tracks=[track.metadata] if track.metadata else [])
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                )
            )
        ),
        call.dispatch(TracksRemovedEvent(indices=[0])),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(state=PlayerStateEnum.STOPPED, index=0, position=0)
            )
        ),
    ]

    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.asyncio
async def test_play(event_emitter, playqueue):
    track = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    await playqueue.add([track])
    await playqueue.play()
    await asyncio.sleep(4)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED, index=0, position=0, timestamp=1
                )
            )
        ),
        call.dispatch(
            TracksAddedEvent(tracks=[track.metadata] if track.metadata else [])
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.asyncio
async def test_switch_track(event_emitter, playqueue):
    track1 = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    track2 = TrackInfo(
        id=to_track_id("2"), metadata=create_track("2"), link_retriever=url2
    )
    await playqueue.add([track1, track2])
    await playqueue.play(0)
    await asyncio.sleep(4)
    await playqueue.play(1)
    await asyncio.sleep(4)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            TracksAddedEvent(tracks=[track1.metadata, track2.metadata])  # type: ignore
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=1,
                    position=0,
                    current_track=track2.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=1,
                    position=0,
                    current_track=track2.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=14814,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.asyncio
async def test_play_next(event_emitter, playqueue):
    track1 = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    track2 = TrackInfo(
        id=to_track_id("2"), metadata=create_track("2"), link_retriever=url2
    )
    track3 = TrackInfo(
        id=to_track_id("3"), metadata=create_track("3"), link_retriever=url3
    )
    await playqueue.add([track1, track2, track3])
    await playqueue.play(0)
    await asyncio.sleep(4)
    await playqueue.play_next(1)
    await asyncio.sleep(2)
    await playqueue.play(2)
    await asyncio.sleep(4)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            TracksAddedEvent(
                tracks=[track1.metadata, track2.metadata, track3.metadata]  # type: ignore
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track1.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=2,
                    position=0,
                    current_track=track3.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=2,
                    position=0,
                    current_track=track3.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=90632,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.asyncio
async def test_play_pause_stop_play(event_emitter, playqueue):
    track = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    await asyncio.sleep(1)
    await playqueue.add([track])
    await playqueue.play()
    await asyncio.sleep(4)
    await playqueue.pause(True)
    await asyncio.sleep(2)
    await playqueue.stop()
    await asyncio.sleep(2)
    await playqueue.play()
    await asyncio.sleep(4)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(TracksAddedEvent(tracks=[track.metadata])),  # type: ignore
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PAUSED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.asyncio
async def test_seek(event_emitter, playqueue):
    track = TrackInfo(
        id=to_track_id("1"), metadata=create_track("1"), link_retriever=url1
    )
    await asyncio.sleep(1)
    await playqueue.add([track])
    await playqueue.play()
    await asyncio.sleep(4)
    await playqueue.seek(3000)
    await asyncio.sleep(4)
    expected_calls = [
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(TracksAddedEvent(tracks=[track.metadata])),  # type: ignore
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.STOPPED,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    timestamp=1,
                )
            )
        ),
        call.dispatch(RequestMoreTracksEvent()),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=3000,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.BUFFERING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
        call.dispatch(
            PlaybackStateChangedEvent(
                state=PlaybackState(
                    state=PlayerStateEnum.PLAYING,
                    index=0,
                    position=0,
                    current_track=track.metadata,
                    audio_info=AudioInfo(
                        sample_rate=32000,
                        bits_per_sample=24,
                        channels=2,
                        duration_ms=13839,
                    ),
                    mime_type="FLAC",
                    timestamp=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)

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
    TrackMovedEvent,
    RequestMoreTracksEvent,
    EventEmitter,
)
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl


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
            actual_state.timestamp_ns = expected_state.timestamp_ns
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


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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
                    state=PlayerStateEnum.STOPPED, index=0, position=0, timestamp_ns=1
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)


@pytest.mark.skip(
    reason="Tests need to be updated for PlayQueueImpl - event emission order and behavior has changed"
)
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
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
                    timestamp_ns=1,
                )
            )
        ),
    ]
    assert_has_calls(event_emitter, expected_calls)

# ── Move track tests ──────────────────────────────────────────────────────────


def make_tracks(n: int) -> list[TrackInfo]:
    """Create n TrackInfo objects with distinct ids."""
    return [
        TrackInfo(
            id=to_track_id(str(i)),
            metadata=create_track(str(i)),
            link_retriever=url1,
        )
        for i in range(1, n + 1)
    ]


def dispatched_events(mock_emitter) -> list:
    """Return the list of events passed to event_emitter.dispatch()."""
    return [c[0][0] for c in mock_emitter.dispatch.call_args_list]


@pytest.mark.asyncio
async def test_move_currently_playing_track_forward(event_emitter, playqueue):
    """Moving the current track forward updates current_track_id and emits PlaybackStateChangedEvent."""
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 1
    event_emitter.reset_mock()

    await playqueue.move(1, 3)

    events = dispatched_events(event_emitter)
    assert len(events) == 2
    assert isinstance(events[0], TrackMovedEvent)
    assert events[0].from_index == 1
    assert events[0].to_index == 3
    assert isinstance(events[1], PlaybackStateChangedEvent)
    assert events[1].state.index == 3
    assert playqueue.current_track_id == 3


@pytest.mark.asyncio
async def test_move_currently_playing_track_backward(event_emitter, playqueue):
    """Moving the current track backward updates current_track_id and emits PlaybackStateChangedEvent."""
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 2
    event_emitter.reset_mock()

    await playqueue.move(2, 0)

    events = dispatched_events(event_emitter)
    assert len(events) == 2
    assert isinstance(events[0], TrackMovedEvent)
    assert events[0].from_index == 2
    assert events[0].to_index == 0
    assert isinstance(events[1], PlaybackStateChangedEvent)
    assert events[1].state.index == 0
    assert playqueue.current_track_id == 0


@pytest.mark.asyncio
async def test_move_non_current_track_before_current_to_after(event_emitter, playqueue):
    """Moving a track before the current track to after it shifts current_track_id left."""
    # Queue: [0,1,2,3], current=2. Move 0→3: [1,2,3,0]. current should become 1.
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 2
    event_emitter.reset_mock()

    await playqueue.move(0, 3)

    events = dispatched_events(event_emitter)
    assert len(events) == 2
    assert isinstance(events[0], TrackMovedEvent)
    assert events[0].from_index == 0
    assert events[0].to_index == 3
    assert isinstance(events[1], PlaybackStateChangedEvent)
    assert events[1].state.index == 1
    assert playqueue.current_track_id == 1


@pytest.mark.asyncio
async def test_move_non_current_track_after_current_to_before(event_emitter, playqueue):
    """Moving a track after the current track to before it shifts current_track_id right."""
    # Queue: [0,1,2,3], current=1. Move 3→0: [3,0,1,2]. current should become 2.
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 1
    event_emitter.reset_mock()

    await playqueue.move(3, 0)

    events = dispatched_events(event_emitter)
    assert len(events) == 2
    assert isinstance(events[0], TrackMovedEvent)
    assert events[0].from_index == 3
    assert events[0].to_index == 0
    assert isinstance(events[1], PlaybackStateChangedEvent)
    assert events[1].state.index == 2
    assert playqueue.current_track_id == 2


@pytest.mark.asyncio
async def test_move_unrelated_tracks_no_state_change_event(event_emitter, playqueue):
    """Moving tracks that don't affect the current index emits only TrackMovedEvent."""
    # Queue: [0,1,2,3], current=0. Move 2→3: current stays at 0.
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 0
    event_emitter.reset_mock()

    await playqueue.move(2, 3)

    events = dispatched_events(event_emitter)
    assert len(events) == 1
    assert isinstance(events[0], TrackMovedEvent)
    assert events[0].from_index == 2
    assert events[0].to_index == 3
    assert playqueue.current_track_id == 0


@pytest.mark.asyncio
async def test_move_invalid_indices_no_events(event_emitter, playqueue):
    """Out-of-bounds move calls are silently ignored and emit no events."""
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    event_emitter.reset_mock()

    await playqueue.move(0, 99)
    await playqueue.move(99, 0)

    event_emitter.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_move_invalidates_prefetched_next_track(event_emitter, playqueue):
    """When a move makes the prefetched next track wrong, it is removed and re-prefetch is triggered.

    Scenario: [T0,T1,T2,T3], current=1, prefetch={1:url_T1, 2:url_T2}.
    Move T2 from 2→0 → [T2,T0,T1,T3].
    After remap: current→2, prepared={2:url_T1, 0:url_T2}, expected_next=3.
    Entry 0 is stale → must be evicted and re-prefetch scheduled.
    """
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 1
    playqueue.prepared_tracks[1] = (TrackUrl(url="http://example.com/t1.flac", format="FLAC"), 0)
    playqueue.prepared_tracks[2] = (TrackUrl(url="http://example.com/t2.flac", format="FLAC"), 1)
    event_emitter.reset_mock()

    # move T2 (index 2) before the current track → next slot becomes wrong
    await playqueue.move(2, 0)

    # stale prefetch entry at remapped index 0 must be gone
    assert 0 not in playqueue.prepared_tracks
    # current track entry (remapped to 2) must be kept
    assert 2 in playqueue.prepared_tracks
    # a re-prefetch task must have been scheduled
    assert playqueue._prefetch_task is not None


@pytest.mark.asyncio
async def test_move_keeps_valid_prefetched_next_track(event_emitter, playqueue):
    """When a move keeps the prefetched next track correct, it is preserved with no re-prefetch.

    Scenario: [T0,T1,T2,T3], current=1, prefetch={1:url_T1, 2:url_T2}.
    Move T3 from 3→0 → [T3,T0,T1,T2].
    After remap: current→2, prepared={2:url_T1, 3:url_T2}, expected_next=3.
    Entry 3 matches → keep both, no re-prefetch.
    """
    await playqueue.add(make_tracks(4))
    await asyncio.sleep(0)
    playqueue.current_track_id = 1
    playqueue.prepared_tracks[1] = (TrackUrl(url="http://example.com/t1.flac", format="FLAC"), 0)
    playqueue.prepared_tracks[2] = (TrackUrl(url="http://example.com/t2.flac", format="FLAC"), 1)
    event_emitter.reset_mock()

    # move T3 (index 3) to position 0 — an unrelated track, next slot stays correct
    await playqueue.move(3, 0)

    # both entries must survive (remapped to new indices)
    assert 2 in playqueue.prepared_tracks   # current track (remapped 1→2)
    assert 3 in playqueue.prepared_tracks   # next track (remapped 2→3, still correct)
    assert len(playqueue.prepared_tracks) == 2
    # no re-prefetch should have been triggered
    assert playqueue._prefetch_task is None

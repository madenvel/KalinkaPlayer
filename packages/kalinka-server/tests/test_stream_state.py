"""Renderer snapshot -> the play queue's StreamState."""

from kalinka_server import renderer_state
from kalinka_server.stream_state import (
    StreamState,
    AudioGraphNodeState,
    StreamErrorSource,
    StreamType,
    from_snapshot,
    to_stream_id,
)


def test_a_playing_snapshot_carries_position_and_format():
    state = from_snapshot(
        renderer_state.empty_state()
        | {
            "playback_state": "playing",
            "position_ms": 4200,
            "format": {
                "sample_rate_hz": 44100,
                "channels": 2,
                "bits_per_sample": 24,
                "stream_kind": "frames",
                "stream_size_units": 9_000_000,
            },
        }
    )

    assert state is not None
    assert state.state is AudioGraphNodeState.STREAMING
    assert state.position == 4200
    assert state.timestamp > 0  # local receipt time, not the renderer's clock
    assert state.stream_info is not None
    assert state.stream_info.format.sample_rate == 44100
    assert state.stream_info.stream_type is StreamType.FRAMES
    assert state.stream_info.stream_size == 9_000_000


def test_a_renderer_that_has_not_reported_yet_translates_to_nothing():
    """empty_state() is 'unspecified' — there is no queue state for it."""
    assert from_snapshot(renderer_state.empty_state()) is None


def test_errors_keep_their_source():
    state = from_snapshot(
        renderer_state.empty_state()
        | {
            "playback_state": "error",
            "error": {"source": "http_stream", "message": "404"},
        }
    )

    assert state is not None
    assert state.state is AudioGraphNodeState.ERROR
    assert state.error is not None
    assert state.error.source is StreamErrorSource.HTTP_STREAM
    assert state.error.message == "404"


def test_a_snapshot_names_the_stream_it_is_about():
    """The token is the stream id the queue minted, stringified for the wire."""
    state = from_snapshot(
        renderer_state.empty_state()
        | {"playback_state": "playing", "source_token": "7"}
    )

    assert state is not None
    assert state.stream_id == 7


def test_a_token_from_elsewhere_names_no_stream_of_ours():
    """Only this Core's tokens are stream ids; another Core's need not be."""
    assert to_stream_id(None) is None
    assert to_stream_id("") is None
    assert to_stream_id("f3a1-not-ours") is None


def test_a_byte_stream_is_not_reported_as_frames():
    state = from_snapshot(
        renderer_state.empty_state()
        | {"playback_state": "paused", "format": {"stream_kind": "bytes"}}
    )

    assert state is not None
    assert state.state is AudioGraphNodeState.PAUSED
    assert state.stream_info is not None
    assert state.stream_info.stream_type is StreamType.BYTES


def _at(state_name: AudioGraphNodeState, position: int, age_ms: int) -> int:
    now = 10_000_000_000
    state = StreamState(
        state=state_name,
        position=position,
        timestamp=now - age_ms * 1_000_000,
    )
    return state.position_at(now)


def test_a_running_stream_has_advanced_since_it_was_last_reported():
    """A renderer names a position only when something changes."""
    assert _at(AudioGraphNodeState.STREAMING, 4200, age_ms=800) == 5000


def test_nothing_else_advances():
    for state in (
        AudioGraphNodeState.PAUSED,
        AudioGraphNodeState.PREPARING,
        AudioGraphNodeState.STOPPED,
        AudioGraphNodeState.FINISHED,
    ):
        assert _at(state, 4200, age_ms=800) == 4200, state


def test_a_timestamp_from_the_future_never_rewinds_playback():
    assert _at(AudioGraphNodeState.STREAMING, 4200, age_ms=-500) == 4200

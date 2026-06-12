import pytest
from unittest.mock import Mock

from native_player.native_player import (
    AudioGraphNodeState,
    StreamError,
    StreamErrorSource,
    StreamState,
)

import kalinka_server.test_tone as test_tone


class FakePlayer:
    """Stand-in for native_player.AudioPlayer: records calls, replays a
    scripted sequence of states from get_state()."""

    instances: list["FakePlayer"] = []

    # Class-level script applied to the next instance.
    next_states: list[StreamState] = []

    def __init__(self, config):
        self.config = dict(config)
        self.appended: list[tuple[str, object]] = []
        self.stopped = False
        self._states = list(FakePlayer.next_states)
        FakePlayer.instances.append(self)

    def append(self, url, fmt):
        self.appended.append((url, fmt))

    def get_state(self):
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def fake_player(monkeypatch):
    FakePlayer.instances = []
    FakePlayer.next_states = [
        StreamState(state=AudioGraphNodeState.STREAMING, position=0),
        StreamState(state=AudioGraphNodeState.FINISHED),
    ]
    monkeypatch.setattr(test_tone, "AudioPlayer", FakePlayer)
    # Keep the poll loop fast in tests.
    monkeypatch.setattr(test_tone, "_POLL_INTERVAL_S", 0)
    return FakePlayer


@pytest.mark.asyncio
async def test_rejects_unknown_channel():
    with pytest.raises(ValueError):
        await test_tone.play_test_tone({}, channel="middle")
    assert FakePlayer.instances == []


@pytest.mark.asyncio
async def test_plays_tone_url_and_releases_player():
    await test_tone.play_test_tone(
        {"output.alsa.device": "default"}, channel="left"
    )
    (player,) = FakePlayer.instances
    (url, _fmt) = player.appended[0]
    assert url.startswith("tone://left?")
    assert player.config["output.alsa.device"] == "default"
    assert player.stopped


@pytest.mark.asyncio
async def test_device_override_applies_without_mutating_input():
    base_config = {"output.alsa.device": "default"}
    await test_tone.play_test_tone(
        base_config, channel="right", device="hw:CARD=DAC,DEV=0"
    )
    (player,) = FakePlayer.instances
    assert player.config["output.alsa.device"] == "hw:CARD=DAC,DEV=0"
    # The shared playqueue config must not be touched.
    assert base_config["output.alsa.device"] == "default"


@pytest.mark.asyncio
async def test_player_error_raises_runtime_error():
    FakePlayer.next_states = [
        StreamState(
            state=AudioGraphNodeState.ERROR,
            error=StreamError(
                source=StreamErrorSource.AUDIO_OUTPUT,
                message="Cannot open audio device",
            ),
        ),
    ]
    with pytest.raises(RuntimeError, match="Cannot open audio device"):
        await test_tone.play_test_tone({}, channel="both")
    (player,) = FakePlayer.instances
    assert player.stopped

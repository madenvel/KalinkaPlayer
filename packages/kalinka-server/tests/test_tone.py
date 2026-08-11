"""The speaker test: a tone on one channel, and what it does to playback."""

import asyncio
from unittest.mock import Mock

import pytest

from kalinka_plugin_sdk import EventEmitter, PlaybackStateChangedEvent
from kalinka_plugin_sdk.datamodel import Album, EntityId, EntityType, PlayerStateEnum
from kalinka_plugin_sdk.inputmodule import Track, TrackInfo, TrackUrl
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl
from kalinka_server.renderer_proto import renderer_pb2 as pb
from kalinka_server.renderer_registry import RendererRegistry, RendererUnavailable
from kalinka_server.renderer_sessions import RendererBusy, SessionPool
from kalinka_server.renderer_test_tone import (
    CHANNELS,
    TONE_DIR,
    TONE_ROUTE,
    TonePlayer,
    tone_uri,
    tone_url,
)

from tests.sim_renderer import SimRenderer


def _track(track_id: str = "1") -> TrackInfo:
    entity = EntityId(id=track_id, type=EntityType.TRACK, source="test_source")

    async def link_retriever() -> TrackUrl:
        return TrackUrl(url=f"http://example/{track_id}.flac", format="FLAC")

    return TrackInfo(
        id=entity,
        metadata=Track(
            id=entity,
            title=f"track{track_id}",
            duration=10,
            album=Album(id=entity, title="album"),
        ),
        link_retriever=link_retriever,
    )


_BASE_URL = "http://10.0.0.5:8000"


def _tone(channel: str) -> str:
    return tone_url(_BASE_URL, channel)


def _sources(renderer: SimRenderer) -> list[str]:
    return [
        command.set_source.source.uri
        for command in renderer.commands
        if command.WhichOneof("op") == "set_source"
    ]


@pytest.fixture
def renderers():
    registry = RendererRegistry(offline_timeout_s=30.0)
    pool = SessionPool(registry, "test-server-id")
    registry.set_on_removed(pool.handle_renderer_removed)
    first = SimRenderer(registry, pool, renderer_id="rid-a")
    second = SimRenderer(registry, pool, renderer_id="rid-b")
    first.connect()
    second.connect()
    return registry, pool, first, second


@pytest.fixture
def emitter():
    return Mock(spec=EventEmitter)


@pytest.fixture
async def queue(renderers, emitter):
    registry, pool, _, _ = renderers
    playqueue = PlayQueueImpl(KalinkaConfig(), emitter, registry, pool)
    await playqueue.__aenter__()
    yield playqueue
    await playqueue.__aexit__(None, None, None)


@pytest.fixture
async def tones(renderers, queue):
    registry, pool, _, _ = renderers
    player = TonePlayer(registry, pool, queue.release_renderer, lambda: _BASE_URL)
    yield player
    await player.shutdown()


async def _play_on(queue, renderer) -> None:
    await queue.add([_track()])
    await queue.play()
    await asyncio.sleep(0.2)
    assert renderer.current is not None, "expected playback to have started"


async def test_the_tone_reaches_the_named_renderer(tones, renderers):
    _registry, _pool, first, second = renderers

    await tones.play("rid-b", "left")

    assert _sources(second) == [_tone("left")]
    assert not _sources(first)
    assert second.session_id is not None


async def test_both_channels_share_one_session(tones, renderers):
    """left then right, two seconds apart: the second replaces the first."""
    _registry, _pool, _first, second = renderers

    await tones.play("rid-b", "left")
    session_id = second.session_id
    await tones.play("rid-b", "right")

    assert _sources(second) == [_tone("left"), _tone("right")]
    assert second.session_id == session_id


async def test_an_unknown_channel_falls_back_to_both(tones, renderers):
    _registry, _pool, _first, second = renderers

    await tones.play("rid-b", "sideways")

    assert _sources(second) == [_tone("both")]


async def test_playback_stops_before_the_tone_and_says_so(
    tones, queue, renderers, emitter
):
    """Deliberate, not silent: the queue reports STOPPED rather than claiming
    to still be playing the track the tone displaced."""
    _registry, _pool, first, _second = renderers
    await _play_on(queue, first)
    emitter.reset_mock()

    await tones.play("rid-a", "left")
    await asyncio.sleep(0.1)

    states = [
        call.args[0].state.state
        for call in emitter.dispatch.call_args_list
        if isinstance(call.args[0], PlaybackStateChangedEvent)
    ]
    assert PlayerStateEnum.STOPPED in states
    assert _sources(first) == [_tone("left")]


async def test_the_tone_does_not_advance_the_queue(tones, queue, renderers):
    """The tone runs on its own session, so its FINISHED is never mistaken for
    the current track ending."""
    _registry, _pool, first, _second = renderers
    await queue.add([_track("1"), _track("2")])
    await queue.play()
    await asyncio.sleep(0.2)

    await tones.play("rid-a", "left")
    first.finish_current()  # the tone runs out
    await asyncio.sleep(0.2)

    assert queue.current_track_id == 0
    assert _sources(first) == [_tone("left")]


async def test_playing_elsewhere_is_left_alone(tones, queue, renderers):
    _registry, _pool, first, second = renderers
    await _play_on(queue, first)
    playing = first.current

    await tones.play("rid-b", "left")
    await asyncio.sleep(0.1)

    assert first.current == playing
    assert first.session_id is not None


async def test_the_session_goes_when_the_tone_ends(tones, renderers):
    """So the queue can claim the renderer again straight after."""
    _registry, _pool, _first, second = renderers
    await tones.play("rid-b", "left")

    second.finish_current()
    await asyncio.sleep(0.05)

    assert second.session_id is None


async def test_the_session_goes_even_if_the_end_is_never_reported(renderers, queue):
    registry, pool, _first, second = renderers
    player = TonePlayer(
        registry, pool, queue.release_renderer, lambda: _BASE_URL, hold_s=0.05
    )

    await player.play("rid-b", "left")
    await asyncio.sleep(0.15)

    assert second.session_id is None


async def test_a_tone_on_another_renderer_ends_the_first(tones, renderers):
    _registry, _pool, first, second = renderers
    await tones.play("rid-a", "left")

    await tones.play("rid-b", "left")

    assert first.session_id is None
    assert second.session_id is not None


async def test_a_renderer_another_core_holds_refuses(tones, renderers):
    _registry, _pool, _first, second = renderers
    second.accept = False

    with pytest.raises(RendererBusy):
        await tones.play("rid-b", "left")


async def test_a_disconnected_renderer_refuses(tones, renderers):
    """It stays in the registry while it is expected back, but has no link."""
    registry, _pool, _first, second = renderers
    registry.disconnect("rid-b", second, clean=False)

    with pytest.raises(RendererUnavailable):
        await tones.play("rid-b", "left")


async def test_the_queue_can_play_again_after_the_tone(tones, queue, renderers):
    _registry, _pool, first, _second = renderers
    await _play_on(queue, first)

    await tones.play("rid-a", "left")
    first.finish_current()  # the tone runs out and the session is released
    await asyncio.sleep(0.1)
    await queue.play()
    await asyncio.sleep(0.2)

    assert first.current is not None
    assert first.commands[-1].WhichOneof("op") != "set_source"


async def test_a_renderer_nothing_is_playing_on_keeps_its_queue(tones, queue, renderers):
    """release_renderer only speaks for the renderer the queue is actually on."""
    _registry, _pool, first, _second = renderers

    assert await queue.release_renderer("rid-a") is False
    await tones.play("rid-a", "left")

    assert first.session_id is not None
    assert _sources(first) == [_tone("left")]


def test_the_tone_url_points_at_a_file_this_server_ships():
    """The mount is mirrored here — standing up create_app() would need a real
    plugin scan."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.staticfiles import StaticFiles

    assert _tone("left") == f"{_BASE_URL}{TONE_ROUTE}/left.flac"

    app = FastAPI()
    app.mount(TONE_ROUTE, StaticFiles(directory=TONE_DIR))
    client = TestClient(app)

    for channel in CHANNELS:
        response = client.get(_tone(channel).removeprefix(_BASE_URL))
        assert response.status_code == 200
        assert response.content[:4] == b"fLaC"


async def test_the_renderer_is_told_the_source_is_flac(tones, renderers):
    """The renderer picks its decoder from the mime type."""
    _registry, _pool, _first, second = renderers

    await tones.play("rid-b", "left")

    set_source = [c for c in second.commands if c.WhichOneof("op") == "set_source"]
    assert set_source[0].set_source.source.mime_type == "audio/flac"


async def test_shutdown_drops_a_sounding_tone(renderers, queue):
    registry, pool, _first, second = renderers
    player = TonePlayer(registry, pool, queue.release_renderer, lambda: _BASE_URL)
    await player.play("rid-b", "left")

    await player.shutdown()

    assert second.session_id is None


def test_the_generated_tone_uri_still_matches_what_renderers_parse():
    """Nothing sends it now, but renderers still accept it (AudioPlayer.cpp)."""
    command = pb.Command()
    command.set_source.source.uri = tone_uri("right")
    assert command.set_source.source.uri == "tone://right?freq=440&duration_ms=3000"

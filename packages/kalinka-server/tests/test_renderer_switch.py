"""Moving playback from one renderer to another."""

import asyncio
from unittest.mock import Mock

import pytest

from kalinka_plugin_sdk import EventEmitter, PlaybackStateChangedEvent
from kalinka_plugin_sdk.datamodel import Album, EntityId, EntityType, PlayerStateEnum
from kalinka_plugin_sdk.inputmodule import Track, TrackInfo, TrackUrl
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl
from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import RendererBusy, SessionPool

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


def _states(emitter) -> list[PlayerStateEnum]:
    return [
        call.args[0].state.state
        for call in emitter.dispatch.call_args_list
        if isinstance(call.args[0], PlaybackStateChangedEvent)
    ]


async def _play_on(queue, renderer) -> None:
    await queue.add([_track()])
    await queue.play()
    await asyncio.sleep(0.2)
    assert renderer.current is not None, "expected playback to have started"


async def test_playback_moves_to_the_selected_renderer(queue, renderers, emitter):
    _registry, _pool, first, second = renderers
    await _play_on(queue, first)

    await queue.switch_renderer("rid-b")
    await asyncio.sleep(0.2)

    assert second.current is not None  # playing there now
    assert first.current is None  # and stopped here
    assert first.session_id is None  # the claim was given up
    assert queue.current_track_id == 0  # same track, from the beginning


async def test_the_switch_is_visible_as_a_stop(queue, renderers, emitter):
    """Agreed behaviour while the position offset is not carried over: the
    track restarts from zero, so pretending playback never stopped would make
    the UI disagree with what is coming out of the speakers."""
    _registry, _pool, first, _second = renderers
    await _play_on(queue, first)
    emitter.reset_mock()

    await queue.switch_renderer("rid-b")
    await asyncio.sleep(0.2)

    states = _states(emitter)
    assert PlayerStateEnum.STOPPED in states
    assert states[-1] == PlayerStateEnum.PLAYING
    assert states.index(PlayerStateEnum.STOPPED) < states.index(PlayerStateEnum.PLAYING)


async def test_a_renderer_that_will_not_have_us_leaves_playback_alone(
    queue, renderers
):
    """The point of claiming the target first: a refusal costs nothing."""
    _registry, _pool, first, second = renderers
    await _play_on(queue, first)
    playing = first.current
    second.accept = False

    with pytest.raises(RendererBusy):
        await queue.switch_renderer("rid-b")
    await asyncio.sleep(0.05)

    assert first.current == playing  # never interrupted
    assert first.session_id is not None
    assert second.session_id is None


async def test_switching_with_nothing_playing_claims_nothing(queue, renderers):
    """Selecting a renderer is not a reason to hold one; the choice takes
    effect at the next play."""
    _registry, _pool, first, second = renderers

    await queue.switch_renderer("rid-b")

    assert first.session_id is None
    assert second.session_id is None


async def test_selecting_an_offline_renderer_leaves_playback_where_it_is(
    queue, renderers
):
    """It resolves back to the renderer already playing, so there is nothing to
    move. The pin still remembers it for when it returns."""
    registry, _pool, first, second = renderers
    await _play_on(queue, first)
    registry.disconnect("rid-b", second, clean=True)

    await queue.switch_renderer("rid-b")

    assert first.current is not None
    assert first.session_id is not None

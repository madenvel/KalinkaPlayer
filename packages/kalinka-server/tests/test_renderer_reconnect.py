"""What clients are told while a renderer's link is down, and on its return."""

import asyncio
from unittest.mock import Mock

import pytest

from kalinka_plugin_sdk import EventEmitter, PlaybackStateChangedEvent
from kalinka_plugin_sdk.datamodel import Album, EntityId, EntityType, PlayerStateEnum
from kalinka_plugin_sdk.inputmodule import Track, TrackInfo, TrackUrl
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl
from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import SessionPool

from tests.sim_renderer import SimRenderer


def _track(track_id: str) -> TrackInfo:
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
def renderer():
    registry = RendererRegistry(offline_timeout_s=30.0)
    pool = SessionPool(registry, "test-server-id")
    registry.set_on_removed(pool.handle_renderer_removed)
    sim = SimRenderer(registry, pool)
    sim.connect()
    return sim


@pytest.fixture
def emitter():
    return Mock(spec=EventEmitter)


@pytest.fixture
async def queue(renderer, emitter):
    playqueue = PlayQueueImpl(
        KalinkaConfig(), emitter, renderer.registry, renderer.pool
    )
    await playqueue.__aenter__()
    yield playqueue
    await playqueue.__aexit__(None, None, None)


def _states(emitter) -> list[PlayerStateEnum]:
    return [
        call.args[0].state.state
        for call in emitter.dispatch.call_args_list
        if isinstance(call.args[0], PlaybackStateChangedEvent)
    ]


def _drop_link(renderer) -> None:
    """What the ws handler does when the socket goes: the session is kept for
    the renderer's return, not closed."""
    renderer.linked = False
    renderer.registry.disconnect(renderer.RENDERER_ID, renderer, clean=False)
    renderer.pool.suspend(renderer.RENDERER_ID, renderer)


def _restore_link(renderer, session_id: str) -> None:
    renderer.linked = True
    renderer.connect()
    return renderer.pool.reconcile(
        renderer_id=renderer.RENDERER_ID,
        reported_session_id=session_id,
        reported_owner_server_id="test-server-id",
        ws_session=renderer,
    )


async def _play(queue, renderer, *tracks: str) -> None:
    await queue.add([_track(t) for t in tracks])
    await queue.play()
    await asyncio.sleep(0.2)
    assert renderer.current is not None


async def test_a_dropped_link_stops_claiming_playback_is_running(
    queue, renderer, emitter
):
    """Nothing was reported before: the position went on advancing on faith
    while the renderer might have been dead."""
    await _play(queue, renderer, "1")
    emitter.reset_mock()

    _drop_link(renderer)
    await asyncio.sleep(0.05)

    assert _states(emitter) == [PlayerStateEnum.BUFFERING]


async def test_a_paused_session_is_not_reported_as_buffering(
    queue, renderer, emitter
):
    """Paused is still true while the link is down, and saying "buffering"
    about a deliberate pause would be worse than saying nothing."""
    await _play(queue, renderer, "1")
    await queue.pause(True)
    await asyncio.sleep(0.1)
    emitter.reset_mock()

    _drop_link(renderer)
    await asyncio.sleep(0.05)

    assert _states(emitter) == []


async def test_the_renderer_coming_back_playing_settles_the_stall(
    queue, renderer, emitter
):
    await _play(queue, renderer, "1")
    session_id = renderer.session_id
    _drop_link(renderer)
    await asyncio.sleep(0.05)
    emitter.reset_mock()

    await _restore_link(renderer, session_id)
    await asyncio.sleep(0.1)

    assert _states(emitter) == [PlayerStateEnum.PLAYING]


async def test_a_track_that_ended_while_we_were_away_is_noticed_on_return(
    queue, renderer, emitter
):
    """The FINISHED itself was dropped at the renderer for want of anywhere to
    send it, so the snapshot on resume is the only evidence it happened."""
    await _play(queue, renderer, "1", "2")
    session_id = renderer.session_id
    _drop_link(renderer)
    renderer.finish_current()  # runs out with nobody listening
    await asyncio.sleep(0.05)
    emitter.reset_mock()

    await _restore_link(renderer, session_id)
    await asyncio.sleep(0.2)

    assert renderer.current is not None  # moved on to track 2
    assert queue.current_track_id == 1


async def test_the_snapshot_at_session_open_is_still_not_republished(
    queue, renderer, emitter
):
    """It only restates the idle baseline; acting on it would emit a STOPPED
    just as playback is about to start."""
    emitter.reset_mock()
    await _play(queue, renderer, "1")

    assert PlayerStateEnum.STOPPED not in _states(emitter)[1:]

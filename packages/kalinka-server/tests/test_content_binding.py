"""Binding a module's asset to a URL, per renderer.

Which address a content link carries cannot be decided when the module hands
the source over: the same resolved source may be played on one renderer and
then re-played on another, reachable on a different interface. So it is decided
here, at the moment a source is handed to a particular renderer, against the
address that renderer itself dialed.
"""

import asyncio
from unittest.mock import Mock

import pytest

from kalinka_plugin_sdk import EventEmitter, PlaybackStateChangedEvent
from kalinka_plugin_sdk.datamodel import (
    Album,
    EntityId,
    EntityType,
    PlayerStateEnum,
    Track,
)
from kalinka_plugin_sdk.inputmodule import (
    DirectUrl,
    ModuleAsset,
    TrackInfo,
    TrackSource,
)
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.playqueue import PlayQueueImpl
from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import SessionPool

from tests.sim_renderer import SimRenderer


def _track(source: TrackSource, track_id: str = "1") -> TrackInfo:
    entity = EntityId(id=track_id, type=EntityType.TRACK, source="localfiles")

    async def source_retriever() -> TrackSource:
        return source

    return TrackInfo(
        id=entity,
        metadata=Track(
            id=entity,
            title=f"track{track_id}",
            duration=10,
            album=Album(id=entity, title="album"),
        ),
        source_retriever=source_retriever,
    )


def _asset_track(track_id: str = "1") -> TrackInfo:
    return _track(
        TrackSource(
            source=ModuleAsset(module="localfiles", asset_id=track_id), format="FLAC"
        ),
        track_id,
    )


@pytest.fixture
def renderers():
    registry = RendererRegistry(offline_timeout_s=30.0)
    pool = SessionPool(registry, "test-server-id")
    registry.set_on_removed(pool.handle_renderer_removed)
    first = SimRenderer(registry, pool, renderer_id="rid-a")
    first.server_addr = ("10.0.0.1", 8000)
    second = SimRenderer(registry, pool, renderer_id="rid-b")
    second.server_addr = ("192.168.5.4", 8000)
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


def _sources(renderer):
    return [
        c.enqueue_source.source
        for c in renderer.commands
        if c.WhichOneof("op") == "enqueue_source"
    ]


async def test_asset_is_addressed_on_the_interface_the_renderer_dialed(
    renderers, queue
):
    _registry, _pool, first, _second = renderers
    await queue.add([_asset_track()])
    await queue.play()
    await asyncio.sleep(0.2)

    assert _sources(first)[0].uri == "http://10.0.0.1:8000/content/localfiles/1"


async def test_the_same_asset_follows_the_renderer_it_moves_to(renderers, queue):
    """The resolved source is reused across renderers, so the host must not be
    baked into it — a link minted for rid-a is unreachable from rid-b."""
    _registry, _pool, first, second = renderers
    await queue.add([_asset_track()])
    await queue.play()
    await asyncio.sleep(0.2)
    await queue.switch_renderer("rid-b")
    await asyncio.sleep(0.2)

    assert _sources(first)[0].uri.startswith("http://10.0.0.1:8000/")
    assert _sources(second)[0].uri == "http://192.168.5.4:8000/content/localfiles/1"


async def test_a_direct_url_reaches_the_renderer_untouched(renderers, queue):
    _registry, _pool, first, _second = renderers
    await queue.add(
        [_track(TrackSource(source=DirectUrl(url="https://cdn.test/1.mp3"), format="MP3"))]
    )
    await queue.play()
    await asyncio.sleep(0.2)

    assert _sources(first)[0].uri == "https://cdn.test/1.mp3"


async def test_the_renderer_is_told_the_format(renderers, queue):
    _registry, _pool, first, _second = renderers
    await queue.add([_asset_track()])
    await queue.play()
    await asyncio.sleep(0.2)

    assert _sources(first)[0].mime_type == "FLAC"


async def test_a_renderer_with_no_known_address_cannot_be_given_content(
    renderers, queue, emitter
):
    """Rather than mint a link on a guessed interface, refuse — the queue
    surfaces it as a playback error."""
    _registry, _pool, first, _second = renderers
    first.server_addr = None
    first.connect()

    await queue.add([_asset_track()])
    await queue.play()
    await asyncio.sleep(0.2)

    assert _sources(first) == []
    assert PlayerStateEnum.ERROR in [
        call.args[0].state.state
        for call in emitter.dispatch.call_args_list
        if isinstance(call.args[0], PlaybackStateChangedEvent)
    ]

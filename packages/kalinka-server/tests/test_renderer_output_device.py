"""The renderer volume device: cached volume while idle, pending apply at
session open, renderer events onto the device bus."""

from __future__ import annotations

import asyncio

import pytest

from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)

from kalinka_server.renderer_output_device import RendererVolumeDevice
from kalinka_server.renderer_registry import RendererRegistry
from kalinka_server.renderer_sessions import SessionPool

from tests.sim_renderer import SimRenderer

# Renderer echoes reach the device via detached callback tasks and the event
# bus delivers to subscribers off the dispatching task; one short yield lets
# both run.
SETTLE_S = 0.05


@pytest.fixture
def renderer():
    registry = RendererRegistry(offline_timeout_s=30.0)
    pool = SessionPool(registry, "test-server-id")
    registry.set_on_removed(pool.handle_renderer_removed)
    sim = SimRenderer(registry, pool)
    sim.connect()
    return sim


@pytest.fixture
def bus():
    bus = EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent](  # type: ignore[type-var]
        initial_state=ExtDeviceState(power_on=False, volume=DeviceVolume())
    )
    yield bus
    bus.close()


class VolumeRecorder:
    def __init__(self, bus: EventBus):
        self.events: list[VolumeChangedEvent] = []
        bus.subscribe([ExtDeviceEventType.VolumeChanged], callback=self._on_item)

    def _on_item(self, item) -> None:
        if isinstance(item, VolumeChangedEvent):
            self.events.append(item)


def make_device(renderer: SimRenderer, bus: EventBus) -> RendererVolumeDevice:
    return RendererVolumeDevice(renderer.registry, renderer.pool, bus)


async def test_idle_volume_cached_and_applied_on_open(renderer, bus):
    recorder = VolumeRecorder(bus)
    device = await make_device(renderer, bus).start()
    try:
        await device.set_volume(25)
        assert (await device.get_volume()).current_volume == 25
        assert renderer.volume == 40  # nothing on the wire without a session
        await asyncio.sleep(SETTLE_S)
        assert recorder.events[-1].volume.current_volume == 25

        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        # Applied inside open(), before any playback command could follow.
        assert renderer.volume == 25
        await session.close()
    finally:
        await device.shutdown()


async def test_set_volume_through_open_session(renderer, bus):
    recorder = VolumeRecorder(bus)
    device = await make_device(renderer, bus).start()
    try:
        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        await device.set_volume(70)
        assert renderer.volume == 70
        await asyncio.sleep(SETTLE_S)  # the renderer's echo drives the cache
        assert (await device.get_volume()).current_volume == 70
        assert recorder.events[-1].volume.current_volume == 70
        await session.close()
    finally:
        await device.shutdown()


async def test_the_volume_mode_is_never_written_to_the_renderers_config(renderer, bus):
    """It rides SessionOpen as a session-scoped policy instead — see
    OutputDeviceRouter.session_volume_policy."""
    device = await make_device(renderer, bus).start()
    try:
        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        assert renderer.config_updates == []
        await session.close()
    finally:
        await device.shutdown()


async def test_snapshot_syncs_cache_and_bus(renderer, bus):
    recorder = VolumeRecorder(bus)
    device = await make_device(renderer, bus).start()
    try:
        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        await session.request_snapshot()
        await asyncio.sleep(SETTLE_S)
        assert (await device.get_volume()).current_volume == 40
        assert recorder.events[-1].volume.current_volume == 40
        await session.close()
    finally:
        await device.shutdown()


async def test_unsupported_volume_reported(renderer, bus):
    renderer.volume_supported = False
    device = await make_device(renderer, bus).start()
    try:
        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        await session.request_snapshot()
        await asyncio.sleep(SETTLE_S)
        assert device.supported_functions() == []
        assert (await device.get_volume()).supported is False
        await session.close()
    finally:
        await device.shutdown()


async def test_cache_survives_session_close(renderer, bus):
    device = await make_device(renderer, bus).start()
    try:
        session = await renderer.pool.open(SimRenderer.RENDERER_ID)
        await device.set_volume(55)
        await asyncio.sleep(SETTLE_S)
        await session.close()
        assert (await device.get_volume()).current_volume == 55
        assert device.supported_functions() == [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
        ]
    finally:
        await device.shutdown()

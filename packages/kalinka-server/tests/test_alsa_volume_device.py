"""Tests for the built-in local-ALSA volume device (alsa_volume_device.py).

These exercise the contract without any real ALSA hardware: a fake native player
(``get_volume``/``set_volume``/``volume_monitor``) stands in for the native
AudioPlayer handed over by ``PlayQueueImpl.create_volume_control_device``, and a
real device ``EventBus`` + recorder verify that volume changes become
``VolumeChangedEvent`` instances — the same path server.py's ``/device/*`` routes
and the device WebSocket use.

Covered:
- set_volume dispatches a matching VolumeChangedEvent (software backend);
- supported_functions / get_volume track the backend's `supported` flag;
- set_volume is a no-op when unsupported;
- an external hardware-mixer change (a knob / amixer) is bridged to the bus;
- software-mode volume persists and is restored on the next start();
- hardware mode does NOT write the software-persistence file (the card owns it);
- the built-in plugin maps volume_type → native mode and builds via the factory.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from typing import List, Optional

import pytest

from kalinka_eventbus.bus import EventBus
from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)

from kalinka_server.alsa_volume_device import (
    AlsaVolumeControlDevice,
    AlsaVolumeOutputConfig,
    AlsaVolumeOutputPlugin,
    AlsaVolumeType,
)

# Backend ids mirror native_player.VolumeBackend.
_HARDWARE = 1
_SOFTWARE = 2
_NONE = 0

DEBOUNCE_SEC = 0.10
SETTLE_MARGIN = 0.30


class _FakeStatus:
    def __init__(self, supported: bool, current: int, maximum: int, backend: int):
        self.supported = supported
        self.current = current
        self.max = maximum
        self.backend = backend


class _FakeMonitor:
    """Mimics the native VolumeMonitor: wait() blocks until push()/stop()."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[int]]" = queue.Queue()
        self._running = True

    def push_external_change(self, percent: int) -> None:
        self._q.put(percent)

    def wait(self) -> _FakeStatus:
        value = self._q.get()
        if value is None:
            return _FakeStatus(False, 0, 100, _NONE)
        return _FakeStatus(True, value, 100, _HARDWARE)

    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False
        self._q.put(None)


class _FakePlayer:
    """Stands in for the native AudioPlayer's volume surface."""

    def __init__(self, *, supported: bool, backend: int, current: int = 50):
        self._supported = supported
        self._backend = backend
        self._current = current
        self.monitor = _FakeMonitor()
        self.sets: List[int] = []

    def get_volume(self) -> _FakeStatus:
        return _FakeStatus(self._supported, self._current, 100, self._backend)

    def set_volume(self, percent: int) -> None:
        self._current = percent
        self.sets.append(percent)

    def volume_monitor(self) -> _FakeMonitor:
        return self.monitor


def _make_bus() -> EventBus:
    return EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent](  # type: ignore[type-var]
        initial_state=ExtDeviceState(power_on=False, volume=DeviceVolume()),
    )


class _VolumeRecorder:
    def __init__(self, bus: EventBus) -> None:
        self._lock = threading.Lock()
        self._events: List[VolumeChangedEvent] = []
        bus.subscribe([ExtDeviceEventType.VolumeChanged], callback=self._on_item)

    def _on_item(self, item) -> None:
        if isinstance(item, VolumeChangedEvent):
            with self._lock:
                self._events.append(item)

    @property
    def volumes(self) -> List[int]:
        with self._lock:
            return [e.volume.current_volume for e in self._events]

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    async def wait_for_count(self, expected: int, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while self.count() < expected:
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.01)

    async def wait_quiescent(self, settle: float) -> None:
        last = self.count()
        await asyncio.sleep(settle)
        while True:
            current = self.count()
            if current == last:
                return
            last = current
            await asyncio.sleep(settle)


async def test_set_volume_dispatches_matching_event():
    bus = _make_bus()
    recorder = _VolumeRecorder(bus)
    device = AlsaVolumeControlDevice(_FakePlayer(supported=True, backend=_SOFTWARE), bus)
    await device.start()
    try:
        await device.set_volume(73)
        await recorder.wait_for_count(1, timeout=1.0)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)
        assert recorder.volumes == [73]
    finally:
        await device.shutdown()
        bus.close()


async def test_supported_functions_track_backend():
    bus = _make_bus()
    supported = AlsaVolumeControlDevice(_FakePlayer(supported=True, backend=_HARDWARE), bus)
    unsupported = AlsaVolumeControlDevice(_FakePlayer(supported=False, backend=_NONE), bus)

    assert supported.supported_functions() == [
        SupportedFunction.GET_VOLUME,
        SupportedFunction.SET_VOLUME,
    ]
    assert unsupported.supported_functions() == []

    vol = await supported.get_volume()
    assert vol.supported is True
    assert (await unsupported.get_volume()).supported is False


async def test_set_volume_noop_when_unsupported():
    bus = _make_bus()
    recorder = _VolumeRecorder(bus)
    player = _FakePlayer(supported=False, backend=_NONE)
    device = AlsaVolumeControlDevice(player, bus)
    await device.start()
    try:
        await device.set_volume(40)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)
        assert player.sets == []
        assert recorder.count() == 0
    finally:
        await device.shutdown()
        bus.close()


async def test_external_hardware_change_is_bridged_to_bus():
    bus = _make_bus()
    recorder = _VolumeRecorder(bus)
    player = _FakePlayer(supported=True, backend=_HARDWARE, current=20)
    device = AlsaVolumeControlDevice(player, bus)
    await device.start()
    try:
        # Simulate a knob / amixer move behind the app's back.
        player.monitor.push_external_change(80)
        await recorder.wait_for_count(1, timeout=1.0)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)
        assert recorder.volumes[-1] == 80
    finally:
        await device.shutdown()
        bus.close()


async def test_software_volume_persists_and_restores(tmp_path):
    state_path = str(tmp_path / "device_volume.json")
    bus = _make_bus()

    player1 = _FakePlayer(supported=True, backend=_SOFTWARE, current=50)
    device1 = AlsaVolumeControlDevice(player1, bus, state_path=state_path)
    await device1.start()
    await device1.set_volume(35)
    await device1.shutdown()

    assert json.load(open(state_path)) == {"volume": 35}

    # A fresh start (e.g. after restart) restores the persisted level.
    player2 = _FakePlayer(supported=True, backend=_SOFTWARE, current=99)
    device2 = AlsaVolumeControlDevice(player2, bus, state_path=state_path)
    await device2.start()
    try:
        assert 35 in player2.sets
        assert (await device2.get_volume()).current_volume == 35
    finally:
        await device2.shutdown()
        bus.close()


async def test_hardware_mode_does_not_write_software_state(tmp_path):
    state_path = str(tmp_path / "device_volume.json")
    bus = _make_bus()
    player = _FakePlayer(supported=True, backend=_HARDWARE, current=10)
    device = AlsaVolumeControlDevice(player, bus, state_path=state_path)
    await device.start()
    try:
        await device.set_volume(60)
        await asyncio.sleep(DEBOUNCE_SEC + SETTLE_MARGIN)
        # Hardware volume is persisted by the card / alsactl, not by us.
        assert not os.path.exists(state_path)
    finally:
        await device.shutdown()
        bus.close()


async def test_rapid_burst_coalesces_to_final_value():
    bus = _make_bus()
    recorder = _VolumeRecorder(bus)
    device = AlsaVolumeControlDevice(_FakePlayer(supported=True, backend=_SOFTWARE), bus)
    await device.start()
    try:
        burst = [5, 12, 19, 26, 33, 40, 47, 54, 61, 68, 77]
        for v in burst:
            await device.set_volume(v)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)
        volumes = recorder.volumes
        assert volumes, "expected at least one event"
        assert volumes[-1] == burst[-1]
        assert len(volumes) < len(burst), "burst should coalesce"
    finally:
        await device.shutdown()
        bus.close()


# --------------------------------------------------------- built-in plugin layer


def test_config_exposes_enabled_and_volume_type():
    cfg = AlsaVolumeOutputConfig()
    assert cfg.enabled is True
    assert cfg.volume_type == AlsaVolumeType.automatic
    # The two user-facing options on the device page.
    assert {"enabled", "volume_type"} <= set(AlsaVolumeOutputConfig.model_fields)


class _FakeQueue:
    """Stands in for PlayQueueImpl: records the configured mode and hands back a
    device built against a fake native player."""

    def __init__(self) -> None:
        self.modes: List[str] = []

    def create_volume_control_device(self, event_emitter, state_path, mode):
        self.modes.append(mode)
        return AlsaVolumeControlDevice(
            _FakePlayer(supported=True, backend=_SOFTWARE),
            event_emitter,
            state_path=state_path,
        )


class _Ctx:
    """Minimal OutputDevicePluginContext stand-in (the plugin only reads
    `emitter` and `config`)."""

    def __init__(self, emitter, config):
        self.emitter = emitter
        self.config = config


@pytest.mark.parametrize(
    "volume_type,expected_mode",
    [
        (AlsaVolumeType.automatic, "auto"),
        (AlsaVolumeType.hardware, "hardware"),
        (AlsaVolumeType.software, "software"),
    ],
)
async def test_plugin_maps_volume_type_and_builds_via_factory(volume_type, expected_mode):
    bus = _make_bus()
    fake_queue = _FakeQueue()
    plugin = AlsaVolumeOutputPlugin()
    plugin.bind(fake_queue, None)
    ctx = _Ctx(emitter=bus, config=AlsaVolumeOutputConfig(volume_type=volume_type))

    await plugin.setup(ctx)  # type: ignore[arg-type]
    try:
        assert fake_queue.modes == [expected_mode]
        assert isinstance(plugin.get_interface(), AlsaVolumeControlDevice)
    finally:
        await plugin.shutdown()
        bus.close()


async def test_plugin_setup_requires_bind():
    plugin = AlsaVolumeOutputPlugin()
    ctx = _Ctx(emitter=_make_bus(), config=AlsaVolumeOutputConfig())
    with pytest.raises(RuntimeError):
        await plugin.setup(ctx)  # type: ignore[arg-type]

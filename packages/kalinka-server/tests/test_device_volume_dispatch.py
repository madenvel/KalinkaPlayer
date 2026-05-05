"""
End-to-end tests for the volume control dispatch path used by server.py.

These tests bypass the HTTP / WebSocket layer and exercise the message-level
contract: when ``device.set_volume()`` is invoked (which is what the server's
``/device/set_volume`` route and the device WebSocket handler ultimately call),
``VolumeChangedEvent`` instances must arrive on the device EventBus.

The DummyDevice debounces rapid changes — these tests verify two guarantees:

1. When changes are spaced apart, every requested volume produces an event
   on the bus (no silent drops).
2. When changes are bursted (and therefore coalesced/throttled), the LAST
   value requested still reaches the bus as the final dispatched event.
"""

from __future__ import annotations

import asyncio
import threading
from typing import List

import pytest

from kalinka_eventbus.bus import EventBus
from kalinka_plugin_dummydevice.dummydevice import DummyDevice
from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)


DEBOUNCE_SEC = 0.10  # mirrors DummyDevice._event_sender_async.debounce_sec
SETTLE_MARGIN = 0.30  # extra slack for debounce window + scheduler jitter


def _make_bus() -> EventBus:
    return EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent](  # type: ignore[type-var]
        initial_state=ExtDeviceState(power_on=False, volume=DeviceVolume()),
    )


class _VolumeRecorder:
    """Capture VolumeChangedEvent instances dispatched on the bus."""

    def __init__(self, bus: EventBus) -> None:
        self._lock = threading.Lock()
        self._events: List[VolumeChangedEvent] = []
        bus.subscribe([ExtDeviceEventType.VolumeChanged], callback=self._on_item)

    def _on_item(self, item) -> None:
        # The first delivery is a ReplayEvent reflecting the bus state at
        # subscription time; only count actual VolumeChangedEvents.
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
        """Wait until at least ``expected`` events have been recorded."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self.count() >= expected:
                return
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.01)

    async def wait_quiescent(self, settle: float) -> None:
        """Wait until no new events arrive for ``settle`` seconds."""
        last = self.count()
        await asyncio.sleep(settle)
        while True:
            current = self.count()
            if current == last:
                return
            last = current
            await asyncio.sleep(settle)


async def _run_with_device(coro_factory):
    bus = _make_bus()
    recorder = _VolumeRecorder(bus)
    device = DummyDevice(bus)
    await device.start()
    try:
        await coro_factory(device, recorder)
    finally:
        await device.shutdown()
        bus.close()


async def test_single_set_volume_dispatches_one_matching_event():
    """A single set_volume call dispatches exactly one event with the same value."""

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        await device.set_volume(73)
        await recorder.wait_for_count(1, timeout=1.0)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        assert recorder.volumes == [73], (
            f"Expected exactly one VolumeChangedEvent with value 73, got {recorder.volumes}"
        )

    await _run_with_device(scenario)


async def test_spaced_set_volume_calls_are_all_dispatched():
    """Volume changes spaced beyond the debounce window must all reach the bus."""

    targets = [10, 35, 60, 85, 42]
    spacing = DEBOUNCE_SEC + SETTLE_MARGIN  # > debounce so each settles independently

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        for v in targets:
            await device.set_volume(v)
            await asyncio.sleep(spacing)

        await recorder.wait_for_count(len(targets), timeout=2.0)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        assert recorder.volumes == targets, (
            f"Each spaced-out set_volume should produce its own event in order. "
            f"Expected {targets}, got {recorder.volumes}"
        )

    await _run_with_device(scenario)


async def test_rapid_burst_coalesces_but_preserves_end_value():
    """A rapid burst is debounced; the last requested value must still appear on the bus.

    This test makes two assertions that together prove the throttling guarantee:

    1. Fewer events arrive on the bus than ``set_volume`` calls were issued —
       i.e. the device actually dropped intermediate values.
    2. Despite that, the final requested value lands on the bus.

    The first assertion guards against a regression where coalescing silently
    breaks (in which case end-value preservation would be trivially satisfied
    because every value, including the last, would be dispatched).
    """

    burst = [5, 12, 19, 26, 33, 40, 47, 54, 61, 68, 77]
    final_value = burst[-1]

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        for v in burst:
            await device.set_volume(v)
            # No sleep between calls — fire faster than the debounce window so
            # the device's _event_sender_async coalesces them.

        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        volumes = recorder.volumes
        assert volumes, "At least one VolumeChangedEvent must be dispatched"
        assert volumes[-1] == final_value, (
            f"Last dispatched volume must equal the final requested value. "
            f"Expected {final_value}, got {volumes[-1]} (full sequence: {volumes})"
        )
        # Coalescing must drop intermediate updates, not amplify them.
        assert len(volumes) <= len(burst), (
            f"Got more dispatched events ({len(volumes)}) than requests ({len(burst)})"
        )
        # Coalescing must actually drop intermediate updates — otherwise the
        # end-value assertion above would pass trivially. Requiring strictly
        # fewer events than requests proves throttling is in effect.
        assert len(volumes) < len(burst), (
            f"Expected the device to throttle/coalesce {len(burst)} rapid requests "
            f"into fewer dispatches, but got all {len(volumes)} of them: {volumes}"
        )

    await _run_with_device(scenario)


async def test_burst_then_settle_then_burst_preserves_each_end_value():
    """Two rapid bursts separated by a quiet gap must each yield their final value.

    Each burst should coalesce internally (intermediate values dropped) while
    the burst's end value still reaches the bus. With two bursts and full
    coalescing we expect exactly two dispatched events: ``burst_a[-1]`` then
    ``burst_b[-1]``. That stricter shape is what we assert here.
    """

    burst_a = [10, 20, 30, 40]
    burst_b = [88, 77, 66, 55]

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        for v in burst_a:
            await device.set_volume(v)
        await asyncio.sleep(DEBOUNCE_SEC + SETTLE_MARGIN)

        for v in burst_b:
            await device.set_volume(v)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        volumes = recorder.volumes
        assert burst_a[-1] in volumes, (
            f"End value of first burst ({burst_a[-1]}) missing from bus stream {volumes}"
        )
        assert volumes[-1] == burst_b[-1], (
            f"Last dispatched volume must equal end of last burst {burst_b[-1]}, "
            f"got {volumes[-1]} (full sequence: {volumes})"
        )
        # Each burst must coalesce — otherwise the end-value assertions above
        # could pass even if intermediates were not throttled.
        assert len(volumes) < len(burst_a) + len(burst_b), (
            f"Expected coalescing across two bursts of "
            f"{len(burst_a)}+{len(burst_b)} requests, but got "
            f"{len(volumes)} dispatches: {volumes}"
        )
        assert volumes == [burst_a[-1], burst_b[-1]], (
            f"Expected exactly the two end values [{burst_a[-1]}, {burst_b[-1]}] "
            f"after full coalescing of both bursts, got {volumes}"
        )

    await _run_with_device(scenario)


async def test_no_events_when_volume_unchanged_after_initial_dispatch():
    """Repeated set_volume to the same value after settling should not re-dispatch."""

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        await device.set_volume(42)
        await recorder.wait_for_count(1, timeout=1.0)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)
        baseline = recorder.count()
        assert baseline == 1
        assert recorder.volumes == [42]

        # Re-issue the same target — DummyDevice gates the dispatch on
        # ``target != last_sent_volume``, so no new event is expected.
        for _ in range(5):
            await device.set_volume(42)
        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        assert recorder.count() == baseline, (
            f"Setting the same volume must not produce additional events; "
            f"events recorded: {recorder.volumes}"
        )

    await _run_with_device(scenario)


@pytest.mark.parametrize("seed", [0, 1, 2])
async def test_random_interleave_end_value_always_matches(seed: int):
    """Mix of fast and slow updates: the final emitted value must match the last request."""
    import random

    rng = random.Random(seed)
    requests: List[int] = []

    async def scenario(device: DummyDevice, recorder: _VolumeRecorder) -> None:
        for _ in range(20):
            v = rng.randint(0, 100)
            requests.append(v)
            await device.set_volume(v)
            # Mostly fast (forces coalescing); occasionally pause past the debounce
            # window to flush an event before the next batch starts.
            if rng.random() < 0.25:
                await asyncio.sleep(DEBOUNCE_SEC + SETTLE_MARGIN)
            else:
                await asyncio.sleep(0.005)

        await recorder.wait_quiescent(DEBOUNCE_SEC + SETTLE_MARGIN)

        volumes = recorder.volumes
        assert volumes, f"No events dispatched for requests {requests}"
        assert volumes[-1] == requests[-1], (
            f"Final dispatched volume {volumes[-1]} must match last request "
            f"{requests[-1]}; full request sequence={requests}, dispatched={volumes}"
        )

    await _run_with_device(scenario)

"""Built-in ALSA volume control used as a silent fallback device.

Unlike the plugin output devices (e.g. MusicCast), this is *not* a plugin: the
native ``AudioPlayer`` is not reachable from plugins, but the local ALSA output
is the basic case that must always have working volume. When no external
output-device plugin is enabled, ``server.py`` uses this object as the active
``ExternalOutputDevice`` so all ``/device/*`` volume traffic is redirected to
the selected ALSA card.

Hardware vs software gain is decided natively from ``output.alsa.volume_mode``
(see AlsaVolumeControl / AudioPlayer): the device just speaks the
``ExternalOutputDevice`` protocol in 0..100 and bridges native volume state to
the device event bus. External mixer changes (a hardware knob, ``amixer``,
another app) are streamed back as ``VolumeChangedEvent`` — the local equivalent
of MusicCast pushing volume events so the UI slider tracks them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional, Protocol

from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)

logger = logging.getLogger(__name__.split(".")[-1])

# Mirrors native_player.VolumeBackend.HARDWARE. Kept as a literal so this module
# (and its tests) need no dependency on the compiled native extension.
_BACKEND_HARDWARE = 1

# Matches DummyDevice: coalesce bursts (slider drags, monitor echoes) into one
# event after a short quiet period.
_DEBOUNCE_SEC = 0.10


class _VolumeStatus(Protocol):
    supported: bool
    current: int
    max: int
    backend: int


class _VolumeMonitor(Protocol):
    def wait(self) -> _VolumeStatus: ...
    def is_running(self) -> bool: ...
    def stop(self) -> None: ...


class VolumeCapablePlayer(Protocol):
    """The slice of PlayQueueImpl / native AudioPlayer this device relies on."""

    def get_output_volume(self) -> _VolumeStatus: ...
    def set_output_volume(self, percent: int) -> None: ...
    def output_volume_monitor(self) -> _VolumeMonitor: ...


class AlsaFallbackDevice(ExternalOutputDevice):
    def __init__(
        self,
        player: VolumeCapablePlayer,
        event_emitter,
        *,
        state_path: Optional[str] = None,
    ):
        """
        Args:
            player: provides native volume get/set + monitor (PlayQueueImpl).
            event_emitter: the ext-device EventBus (dispatch / set_initial_state).
            state_path: file used to persist the software-mode volume across
                restarts; ``None`` disables persistence. Hardware volume is
                persisted by the card / alsactl, so it is never written here.
        """
        self._player = player
        self._event_emitter = event_emitter
        self._state_path = state_path

        self._target_volume = 0
        self._volume_changed = asyncio.Event()
        self._sender_task: Optional[asyncio.Task] = None
        self._monitor: Optional[_VolumeMonitor] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._shutdown = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> "AlsaFallbackDevice":
        status = self._status()

        # Software mode owns the value entirely (nothing external sets it), so
        # restore the last level instead of jumping to full-scale on restart.
        if status.supported and status.backend != _BACKEND_HARDWARE:
            restored = self._load_persisted_volume()
            if restored is not None:
                self._player.set_output_volume(restored)
                status = self._status()

        self._target_volume = status.current
        self._event_emitter.set_initial_state(
            ExtDeviceState(power_on=True, volume=self._to_device_volume(status))
        )

        self._sender_task = asyncio.create_task(self._event_sender_async())

        # Only a hardware mixer can change behind our back; software/fixed don't
        # need (and the native monitor won't produce) external events.
        if status.supported and status.backend == _BACKEND_HARDWARE:
            self._monitor = self._player.output_volume_monitor()
            self._monitor_task = asyncio.create_task(self._monitor_async())

        logger.info(
            "ALSA fallback device started (supported=%s, backend=%s, volume=%d)",
            status.supported,
            status.backend,
            status.current,
        )
        return self

    async def shutdown(self) -> None:
        self._shutdown = True
        self._volume_changed.set()  # wake the sender
        if self._monitor is not None:
            self._monitor.stop()  # wake the blocking wait()
        for task in (self._sender_task, self._monitor_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    # --------------------------------------------------- ExternalOutputDevice API
    async def get_volume(self) -> DeviceVolume:
        return self._to_device_volume(self._status())

    async def set_volume(self, volume: int) -> None:
        status = self._status()
        if not status.supported:
            logger.debug("set_volume ignored: ALSA volume control not available")
            return
        volume = max(0, min(int(volume), status.max))
        self._player.set_output_volume(volume)
        self._target_volume = volume
        if status.backend != _BACKEND_HARDWARE:
            self._persist_volume(volume)
        self._volume_changed.set()

    async def power_on(self) -> None:
        # A sound card has no power state; accept the call as a no-op.
        return None

    async def is_power_on(self) -> bool:
        return True

    async def power_off(self) -> None:
        return None

    def supported_functions(self) -> list[SupportedFunction]:
        if self._status().supported:
            return [SupportedFunction.GET_VOLUME, SupportedFunction.SET_VOLUME]
        return []

    # ------------------------------------------------------------------ internals
    def _status(self) -> _VolumeStatus:
        return self._player.get_output_volume()

    @staticmethod
    def _to_device_volume(status: _VolumeStatus) -> DeviceVolume:
        return DeviceVolume(
            max_volume=status.max,
            current_volume=status.current,
            volume_gain=0,
            supported=status.supported,
        )

    async def _monitor_async(self) -> None:
        """Turn external hardware-mixer changes into device-bus events."""
        assert self._monitor is not None
        try:
            while not self._shutdown and self._monitor.is_running():
                status = await asyncio.to_thread(self._monitor.wait)
                if self._shutdown or not status.supported:
                    break
                self._target_volume = status.current
                self._volume_changed.set()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the listener die silently
            logger.error("ALSA volume monitor error: %s", e)

    async def _event_sender_async(self) -> None:
        """Debounce/coalesce volume changes onto the device bus (cf. DummyDevice)."""
        last_sent: Optional[int] = None
        try:
            while not self._shutdown:
                await self._volume_changed.wait()
                self._volume_changed.clear()
                if self._shutdown:
                    break

                # Collapse a burst: wait for a quiet window before emitting.
                try:
                    while True:
                        await asyncio.wait_for(
                            self._volume_changed.wait(), timeout=_DEBOUNCE_SEC
                        )
                        self._volume_changed.clear()
                        if self._shutdown:
                            break
                except asyncio.TimeoutError:
                    pass
                if self._shutdown:
                    break

                target = self._target_volume
                if target != last_sent:
                    status = self._status()
                    self._event_emitter.dispatch(
                        VolumeChangedEvent.model_construct(
                            event_type=ExtDeviceEventType.VolumeChanged,
                            volume=DeviceVolume(
                                max_volume=status.max,
                                current_volume=target,
                                volume_gain=0,
                                supported=status.supported,
                            ),
                        )
                    )
                    last_sent = target
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------- software-volume persistence
    def _persist_volume(self, volume: int) -> None:
        if self._state_path is None:
            return
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"volume": int(volume)}, f)
            os.replace(tmp, self._state_path)
        except OSError as e:
            logger.warning("Could not persist ALSA software volume: %s", e)

    def _load_persisted_volume(self) -> Optional[int]:
        if self._state_path is None:
            return None
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            return max(0, min(int(data["volume"]), 100))
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None

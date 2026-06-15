"""Built-in ALSA volume control, exposed as a local output device.

Unlike the entry-point output-device plugins (e.g. MusicCast), the native
``AudioPlayer`` is not reachable from plugins — but the local ALSA output is the
basic case that must always have working volume. So this ships in-process as a
built-in device (``local-alsa``), registered first and used when no other device
is enabled. ``player_setup`` injects the play queue (which owns the native
player) via :meth:`AlsaVolumeOutputPlugin.bind`.

The device speaks the ``ExternalOutputDevice`` protocol in 0..100 and bridges
native volume state to the device event bus. Hardware vs software gain is decided
natively (see AlsaVolumeControl / AudioPlayer) from the ``volume_type`` chosen on
the device's settings; disabling the device leaves the player at unity ("fixed",
bit-perfect). External mixer changes (a hardware knob, ``amixer``, another app)
are streamed back as ``VolumeChangedEvent`` — the local equivalent of MusicCast
pushing volume events so the UI slider tracks them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from enum import Enum
from typing import Any, ClassVar, Optional, Protocol

from pydantic import Field

from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.plugin import OutputDevicePlugin, OutputDevicePluginContext

logger = logging.getLogger(__name__.split(".")[-1])

# Mirrors native_player.VolumeBackend.HARDWARE. Kept as a literal so this module
# (and its tests) need no dependency on the compiled native extension.
_BACKEND_HARDWARE = 1

# Matches DummyDevice: coalesce bursts (slider drags, monitor echoes) into one
# event after a short quiet period.
_DEBOUNCE_SEC = 0.10


class AlsaVolumeType(str, Enum):
    """Active volume-control backend. "fixed" is not here — it's the disabled
    state of the device (no volume control, bit-perfect)."""

    automatic = "automatic"
    hardware = "hardware"
    software = "software"


# AlsaVolumeType → native AudioPlayer.configure_volume() mode string.
_VOLUME_TYPE_TO_MODE = {
    AlsaVolumeType.automatic: "auto",
    AlsaVolumeType.hardware: "hardware",
    AlsaVolumeType.software: "software",
}


class AlsaVolumeOutputConfig(ModuleConfig):
    """Settings for the built-in local ALSA output: the inherited ``enabled``
    toggle (disabled ⇒ fixed/bit-perfect) plus the volume-control type."""

    __module_icon__: ClassVar[str] = "speaker_outlined"
    name: str = Field(default="local-alsa", frozen=True, exclude=True)
    volume_type: AlsaVolumeType = Field(
        default=AlsaVolumeType.automatic,
        title="Volume control type",
        json_schema_extra={
            "help": (
                "Automatic uses the card's hardware mixer when it has one "
                "(bit-perfect) and otherwise applies software gain. Hardware / "
                "Software force one of them. Disable this device for a fixed, "
                "bit-perfect output (control the volume downstream)."
            ),
            "importance": "simple",
        },
    )


class _VolumeStatus(Protocol):
    supported: bool
    current: int
    max: int
    backend: int


class _VolumeMonitor(Protocol):
    def wait(self) -> _VolumeStatus: ...
    def is_running(self) -> bool: ...
    def stop(self) -> None: ...


class NativeVolumePlayer(Protocol):
    """The native ``AudioPlayer`` volume surface this device drives (handed over
    by ``PlayQueueImpl.create_volume_control_device``). A test double only needs
    these three methods."""

    def get_volume(self) -> _VolumeStatus: ...
    def set_volume(self, volume: int) -> None: ...
    def volume_monitor(self) -> _VolumeMonitor: ...


class AlsaVolumeControlDevice(ExternalOutputDevice):
    def __init__(
        self,
        player: NativeVolumePlayer,
        event_emitter,
        *,
        state_path: Optional[str] = None,
    ):
        """
        Args:
            player: the native AudioPlayer (volume get/set + monitor).
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
    async def start(self) -> "AlsaVolumeControlDevice":
        status = self._status()

        # Software mode owns the value entirely (nothing external sets it), so
        # restore the last level instead of jumping to full-scale on restart.
        if status.supported and status.backend != _BACKEND_HARDWARE:
            restored = self._load_persisted_volume()
            if restored is not None:
                self._player.set_volume(restored)
                status = self._status()

        self._target_volume = status.current
        self._event_emitter.set_initial_state(
            ExtDeviceState(power_on=True, volume=self._to_device_volume(status))
        )

        self._sender_task = asyncio.create_task(self._event_sender_async())

        # Only a hardware mixer can change behind our back; software/fixed don't
        # need (and the native monitor won't produce) external events.
        if status.supported and status.backend == _BACKEND_HARDWARE:
            self._monitor = self._player.volume_monitor()
            self._monitor_task = asyncio.create_task(self._monitor_async())

        logger.info(
            "Local ALSA volume device started (supported=%s, backend=%s, volume=%d)",
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
        self._player.set_volume(volume)
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
        return self._player.get_volume()

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


class AlsaVolumeOutputPlugin(OutputDevicePlugin):
    """Built-in output device for the local ALSA card.

    Not loaded via entry points — ``player_setup`` constructs it, calls
    :meth:`bind` to inject the play queue + persistence path (which the plugin
    context can't carry), then runs the normal ``setup()``/``shutdown()``
    lifecycle so it participates in the device registry and settings page.
    """

    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "local-alsa"
    CONFIG_MODEL = AlsaVolumeOutputConfig

    def __init__(self):
        self._device: Optional[AlsaVolumeControlDevice] = None
        # Untyped on purpose: this is the concrete PlayQueueImpl (it carries the
        # factory), but annotating it as such would import playqueue and create a
        # cycle. The play queue is injected by player_setup, so Any is fine here.
        self._playqueue: Any = None
        self._state_path: Optional[str] = None

    def bind(self, playqueue: Any, state_path: Optional[str]) -> None:
        self._playqueue = playqueue
        self._state_path = state_path

    async def setup(self, context: OutputDevicePluginContext) -> None:
        if self._playqueue is None:
            raise RuntimeError(
                "AlsaVolumeOutputPlugin.bind() must be called before setup()"
            )
        config = context.config
        volume_type = getattr(config, "volume_type", AlsaVolumeType.automatic)
        mode = _VOLUME_TYPE_TO_MODE.get(volume_type, "auto")
        self._device = self._playqueue.create_volume_control_device(
            event_emitter=context.emitter,
            state_path=self._state_path,
            mode=mode,
        )
        await self._device.start()

    async def shutdown(self) -> None:
        if self._device is not None:
            await self._device.shutdown()
        self._device = None

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

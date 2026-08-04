"""Which device module owns the output controls right now.

A renderer controls its own volume and power through the built-in renderer
device unless it has been delegated to another module — an amp wired
downstream of that renderer's output. Delegation is per renderer, so the
module clients talk to follows the active renderer instead of being fixed at
startup: switching renderers switches whose volume the slider drives.

Every device module shares one event bus, so the router also gates what
reaches clients. A module that is enabled but does not own the active
renderer stays live — a MusicCast amp reports its own volume changes whether
or not Kalinka is playing — but its events are dropped instead of being
broadcast as if they described the current output. When the owner changes,
:meth:`OutputDeviceRouter.resync` re-authors the bus state from whoever is in
charge now, so clients are not left showing the previous module's numbers.

A module that is missing, not READY, or not an output device resolves to None,
which every caller reads as "no control available".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Mapping, Optional

from kalinka_plugin_sdk import ModuleHealthState
from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction
from kalinka_plugin_sdk.ext_device_events import ExtDeviceEvent, ExtDeviceState

from .renderer_output_device import (
    DEFAULT_VOLUME,
    RendererOutputPlugin,
    RendererVolumeStyle,
    wire_volume_mode,
)
from .renderer_registry import RendererRegistry

if TYPE_CHECKING:  # avoids a cycle: player_setup builds the router
    from kalinka_eventbus import EventBus

    from .player_setup import PreparedPlugin

logger = logging.getLogger(__name__.split(".")[-1])


class RoutedDeviceEmitter:
    """The emitter one device plugin is handed. Forwards to the shared bus
    only while that plugin owns the active renderer's output."""

    def __init__(self, plugin_id: str, bus: "EventBus", router: "OutputDeviceRouter"):
        self._plugin_id = plugin_id
        self._bus = bus
        self._router = router

    def _owns_output(self) -> bool:
        return self._router.current_name() == self._plugin_id

    def dispatch(self, event: ExtDeviceEvent) -> None:
        if not self._owns_output():
            return
        self._bus.dispatch(event)

    def set_initial_state(self, state: ExtDeviceState) -> None:
        if not self._owns_output():
            return
        self._bus.set_initial_state(state)


class _FilteredStream:
    """Passes through only what arrives while the owning check holds."""

    def __init__(self, inner, owns: Callable[[], bool]):
        self._inner = inner
        self._stream = None
        self._owns = owns

    async def __aenter__(self):
        self._stream = await self._inner.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._inner.__aexit__(exc_type, exc, tb)

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            item = await self._stream.__anext__()
            if self._owns():
                return item


class RoutedPlaybackListener:
    """The playback-event listener one device plugin is handed.

    A module that does not own the active renderer's output hears nothing: an
    amp wired to one renderer has no business reacting to a track playing on
    another, which would otherwise have it adjusting its own volume for
    somebody else's playback.
    """

    def __init__(self, plugin_id: str, bus, router: "OutputDeviceRouter"):
        self._plugin_id = plugin_id
        self._bus = bus
        self._router = router

    def _owns_output(self) -> bool:
        return self._router.current_name() == self._plugin_id

    def stream(self, event_types):
        return _FilteredStream(self._bus.stream(event_types), self._owns_output)

    def subscribe(self, event_types, callback=None, listener_id=None):
        if callback is None:
            return self._bus.subscribe(event_types, None, listener_id)

        def _gated(item):
            if self._owns_output():
                callback(item)

        return self._bus.subscribe(event_types, _gated, listener_id)

    def unsubscribe(self, listener_id: str) -> None:
        self._bus.unsubscribe(listener_id)

    def get_snapshot(self):
        return self._bus.get_snapshot()


class OutputDeviceRouter:
    def __init__(
        self,
        registry: RendererRegistry,
        devices: Callable[[], Mapping[str, "PreparedPlugin"]],
        bus: Optional["EventBus"] = None,
    ):
        self._registry = registry
        self._devices = devices
        self._bus = bus

    def current_name(self) -> str:
        """Plugin id of the module in charge, delegated or not."""
        active = self._registry.active_id()
        delegate = self._registry.volume_control(active) if active else None
        return delegate or RendererOutputPlugin.PLUGIN_ID

    def current(self) -> Optional[ExternalOutputDevice]:
        prepared = self._devices().get(self.current_name())
        if prepared is None or prepared.health_state is not ModuleHealthState.READY:
            return None
        interface = prepared.interface
        return interface if isinstance(interface, ExternalOutputDevice) else None

    def _renderer_config(self):
        prepared = self._devices().get(RendererOutputPlugin.PLUGIN_ID)
        context = getattr(prepared, "plugin_context", None)
        return getattr(context, "config", None)

    def session_volume_policy(self, renderer_id: str) -> tuple[str, Optional[int]]:
        """What SessionOpen should carry for this renderer: (mode, percent).

        A delegated renderer is fixed at full scale — the amp downstream owns
        the level, and attenuating twice would cost headroom and, in software
        mode, resolution.

        Otherwise the renderer device's ``volume_style`` decides the mode, and
        "renderer choice" sends none at all. A level is sent when the style is
        "fixed" (that *is* the level), and once per renderer this server has
        never played through, so a new one starts somewhere safe rather than
        wherever its mixer happened to be left.
        """
        if self._registry.volume_control(renderer_id):
            return (wire_volume_mode(RendererVolumeStyle.fixed), 100)

        config = self._renderer_config()
        style = getattr(config, "volume_style", RendererVolumeStyle.renderer)
        default_volume = getattr(config, "default_volume", DEFAULT_VOLUME)
        if style is RendererVolumeStyle.fixed:
            return (wire_volume_mode(style), default_volume)
        if not self._registry.volume_seeded(renderer_id):
            return (wire_volume_mode(style), default_volume)
        return (wire_volume_mode(style), None)

    def emitter_for(self, plugin_id: str) -> Optional[RoutedDeviceEmitter]:
        if self._bus is None:
            return None
        return RoutedDeviceEmitter(plugin_id, self._bus, self)

    def listener_for(self, plugin_id: str, bus) -> RoutedPlaybackListener:
        return RoutedPlaybackListener(plugin_id, bus, self)

    async def resync(self) -> None:
        """Publish the current owner's state, so clients stop showing the
        numbers of a module that no longer drives the output."""
        if self._bus is None:
            return
        device = self.current()
        if device is None:
            state = ExtDeviceState(
                power_on=False, volume=DeviceVolume(supported=False)
            )
        else:
            functions = device.supported_functions()
            try:
                volume = (
                    await device.get_volume()
                    if SupportedFunction.GET_VOLUME in functions
                    else DeviceVolume(supported=False)
                )
                power_on = (
                    await device.is_power_on()
                    if SupportedFunction.IS_POWER_ON in functions
                    else True
                )
            except Exception as e:
                logger.warning(
                    "Could not read state from '%s': %s", self.current_name(), e
                )
                return
            state = ExtDeviceState(power_on=power_on, volume=volume)
        self._bus.set_initial_state(state)

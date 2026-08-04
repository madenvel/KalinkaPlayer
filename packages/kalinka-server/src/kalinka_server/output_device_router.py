"""Which device module owns the output controls right now.

A renderer controls its own volume and power through the built-in renderer
device unless it has been delegated to another module — an amp wired
downstream of that renderer's output. Delegation is per renderer, so the
module clients talk to follows the active renderer instead of being fixed at
startup: switching renderers switches whose volume the slider drives.

A module that is missing, not READY, or not an output device resolves to None,
which every caller reads as "no control available".
"""

from __future__ import annotations

import logging
from typing import Callable, Mapping, Optional

from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .player_setup import ModuleHealthState, PreparedPlugin
from .renderer_output_device import RendererOutputPlugin
from .renderer_registry import RendererRegistry

logger = logging.getLogger(__name__.split(".")[-1])


class OutputDeviceRouter:
    def __init__(
        self,
        registry: RendererRegistry,
        devices: Callable[[], Mapping[str, PreparedPlugin]],
    ):
        self._registry = registry
        self._devices = devices

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

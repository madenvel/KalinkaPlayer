from typing import Optional
from kalinka_plugin_sdk.api import (
    PluginContext,
    OutputDevicePlugin,
)  # runtime Protocols
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .config_model import DummydeviceConfig
from .dummydevice import DummyDevice


class KalinkaPluginDummydevice(OutputDevicePlugin):
    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "dummydevice"
    CONFIG_MODEL = DummydeviceConfig

    def __init__(self):
        self._device = None

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

    def setup(self, context: PluginContext) -> None:
        self._device = DummyDevice(context.event_emitter)

    def shutdown(self) -> None:
        pass

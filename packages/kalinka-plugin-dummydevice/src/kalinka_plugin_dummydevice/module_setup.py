from typing import Optional
from kalinka_plugin_sdk.plugin import (
    OutputDevicePluginContext,
    OutputDevicePlugin,
)  # runtime Protocols
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .config_model import DummydeviceConfig
from .dummydevice import DummyDevice


class KalinkaPluginDummydevice(OutputDevicePlugin):
    REQUIRES_SDK = ">=2,<3"
    PLUGIN_ID = "dummydevice"
    CONFIG_MODEL = DummydeviceConfig

    def __init__(self):
        self._device = None

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

    async def setup(self, context: OutputDevicePluginContext) -> None:
        self._device = DummyDevice(context.emitter)
        await self._device.start()

    async def shutdown(self) -> None:
        if self._device:
            await self._device.shutdown()
        self._device = None

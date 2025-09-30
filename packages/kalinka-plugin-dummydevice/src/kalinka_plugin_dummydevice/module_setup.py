from kalinka_plugin_sdk.api import PluginContext  # runtime Protocols
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .config_model import DummydeviceConfig
from .dummydevice import DummyDevice


REQUIRES_SDK = ">=1.0,<2"
PLUGIN_ID = "dummydevice"

Config = DummydeviceConfig


def setup(
    config: DummydeviceConfig,
    context: PluginContext,
) -> ExternalOutputDevice:
    global device
    device = DummyDevice(context.event_emitter)

    return device


def shutdown():
    pass

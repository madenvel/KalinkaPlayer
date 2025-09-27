from addons.device.dummy.dummydevice import DummyDevice
from addons.device.dummy.config_model import DummyDeviceConfig
from sdk.api import PluginContext


device = None

Config = DummyDeviceConfig


def setup(
    config: DummyDeviceConfig,
    context: PluginContext,
):
    global device
    device = DummyDevice(context.events)

    return device


def shutdown():
    pass

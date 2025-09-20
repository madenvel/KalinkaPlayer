from addons.device.dummy.dummydevice import DummyDevice
from src.async_common import EventEmitter
from addons.device.dummy.config_model import DummyDeviceConfig


device = None

Config = DummyDeviceConfig


def setup(
    config: DummyDeviceConfig, playqueue, event_emitter: EventEmitter, event_listener
):
    global device
    device = DummyDevice(event_emitter)

    return device


def shutdown():
    pass

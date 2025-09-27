from addons.device.dummy.dummydevice import DummyDevice
from addons.device.dummy.config_model import DummyDeviceConfig
from sdk.api import PlayQueueAPI, EventEmitterAPI, EventListenerAPI


device = None

Config = DummyDeviceConfig


def setup(
    config: DummyDeviceConfig,
    playqueue: PlayQueueAPI,
    event_emitter: EventEmitterAPI,
    event_listener: EventListenerAPI,
):
    global device
    device = DummyDevice(event_emitter)

    return device


def shutdown():
    pass

from addons.device.musiccast.config_model import MusicCastConfig
from addons.device.musiccast.musiccast import Device
from sdk.api import PluginContext
from sdk.events import EventType


device = None
device_subscriptions = []

Config = MusicCastConfig


def setup(config: MusicCastConfig, context: PluginContext):
    global device
    device = Device(config, context.playqueue, context.events)
    device_subscriptions.append(
        context.listener.subscribe(EventType.StateChanged, device._on_state_changed)
    )

    return device


def shutdown():
    global device_subscriptions, device
    for subscription in device_subscriptions:
        subscription.unsubscribe()

    device_subscriptions.clear()
    device = None

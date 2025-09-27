from addons.device.musiccast.config_model import MusicCastConfig
from addons.device.musiccast.musiccast import Device
from sdk.api import PlayQueueAPI, EventEmitterAPI, EventListenerAPI
from sdk.events import EventType


device = None
device_subscriptions = []

Config = MusicCastConfig


def setup(
    config: MusicCastConfig,
    playqueue: PlayQueueAPI,
    event_emitter: EventEmitterAPI,
    event_listener: EventListenerAPI,
):
    global device
    device = Device(config, playqueue, event_emitter)
    device_subscriptions.append(
        event_listener.subscribe(EventType.StateChanged, device._on_state_changed)
    )

    return device


def shutdown():
    global device_subscriptions, device
    for subscription in device_subscriptions:
        subscription.unsubscribe()

    device_subscriptions.clear()
    device = None

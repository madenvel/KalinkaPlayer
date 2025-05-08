from addons.device.musiccast.musiccast import Device
from src.async_common import EventEmitter, EventListener
from src.config import Config
from src.playqueue import PlayQueue, EventType

device = None
device_subscriptions = []


def setup(
    config: Config,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
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

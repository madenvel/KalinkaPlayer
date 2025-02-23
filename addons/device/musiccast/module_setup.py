from addons.device.musiccast.musiccast import Device
from src.async_common import EventEmitter, EventListener
from src.config import Config
from src.playqueue import PlayQueue, EventType


def setup(
    config: Config,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
):
    device = Device(config, playqueue, event_emitter)
    event_listener.subscribe(EventType.StateChanged, device._on_state_changed)

    return device

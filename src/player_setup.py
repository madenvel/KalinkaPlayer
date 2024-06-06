from addons.input_module.qobuz.qobuz_reporter import QobuzReporter
from src.rpiasync import EventEmitter, EventListener
from queue import Queue
from addons.input_module.qobuz.qobuz_autoplay import QobuzAutoplay
from addons.input_module.qobuz import (
    QobuzInputModule,
    get_client,
)

from src.playqueue import EventType, PlayQueue
from src.inputmodule import InputModule
from addons.device.musiccast.musiccast import Device


def setup_autoplay(
    client,
    playqueue: PlayQueue,
    track_browser: InputModule,
    event_listener: EventListener,
):
    autoplay = QobuzAutoplay(client, playqueue, track_browser)
    event_listener.subscribe(EventType.RequestMoreTracks, autoplay.add_recommendation)
    event_listener.subscribe(EventType.TracksAdded, autoplay.add_tracks)
    event_listener.subscribe(EventType.TracksRemoved, autoplay.remove_tracks)


def setup_reporter(
    client,
    event_listener: EventListener,
):
    reporter = QobuzReporter(client)
    event_listener.subscribe(EventType.StateChanged, reporter.on_state_changed)


def setup_device(
    playqueue: PlayQueue, event_listener: EventListener, event_emitter: EventEmitter
):
    device = Device(playqueue, event_emitter)
    event_listener.subscribe(EventType.StateChanged, device._on_state_changed)

    return device


def setup():
    client = get_client()
    queue = Queue()
    event_emitter = EventEmitter(queue)
    event_listener = EventListener(queue)
    inputmodule = QobuzInputModule(client, event_emitter)
    playqueue = PlayQueue(event_emitter)
    setup_reporter(client, event_listener)
    setup_autoplay(client, playqueue, inputmodule, event_listener)
    device = setup_device(playqueue, event_listener, event_emitter)

    return playqueue, event_listener, inputmodule, device

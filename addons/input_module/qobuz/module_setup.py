from addons.input_module.qobuz.qobuz_reporter import QobuzReporter
from src.async_common import EventEmitter, EventListener
from addons.input_module.qobuz.qobuz_autoplay import QobuzAutoplay
from addons.input_module.qobuz import (
    QobuzInputModule,
    get_client,
)

from src.playqueue import EventType, PlayQueue
from src.inputmodule import InputModule


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


def setup(
    playqueue: PlayQueue, event_emitter: EventEmitter, event_listener: EventListener
):
    client = get_client()
    inputmodule = QobuzInputModule(client, event_emitter)
    setup_autoplay(client, playqueue, inputmodule, event_listener)
    setup_reporter(client, event_listener)

    return inputmodule

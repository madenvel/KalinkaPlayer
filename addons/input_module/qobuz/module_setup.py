from addons.input_module.qobuz.qobuz_reporter import QobuzReporter
from src.async_common import EventEmitter, EventListener
from addons.input_module.qobuz.qobuz_autoplay import QobuzAutoplay
from addons.input_module.qobuz import (
    QobuzInputModule,
    get_client,
)

from src.config import Config
from src.playqueue import EventType, PlayQueue
from src.inputmodule import InputModule

autoplay = None
reporter = None
# Store subscriptions for cleanup
autoplay_subscriptions = []
reporter_subscriptions = []


def setup_autoplay(
    client,
    playqueue: PlayQueue,
    track_browser: InputModule,
    event_listener: EventListener,
):
    global autoplay, autoplay_subscriptions

    autoplay = QobuzAutoplay(client, playqueue, track_browser)
    autoplay_subscriptions.append(
        event_listener.subscribe(
            EventType.RequestMoreTracks, autoplay.add_recommendation
        )
    )
    autoplay_subscriptions.append(
        event_listener.subscribe(EventType.TracksAdded, autoplay.add_tracks)
    )
    autoplay_subscriptions.append(
        event_listener.subscribe(EventType.TracksRemoved, autoplay.remove_tracks)
    )


def setup_reporter(
    client,
    event_listener: EventListener,
):
    global reporter, reporter_subscriptions

    reporter = QobuzReporter(client)
    reporter_subscriptions.append(
        event_listener.subscribe(EventType.StateChanged, reporter.on_state_changed)
    )


def setup(
    config: Config,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
):
    client = get_client(config)
    inputmodule = QobuzInputModule(config, client, event_emitter)
    setup_autoplay(client, playqueue, inputmodule, event_listener)
    setup_reporter(client, event_listener)

    return inputmodule


def shutdown():
    global autoplay, reporter, autoplay_subscriptions, reporter_subscriptions

    # Unsubscribe from all autoplay event subscriptions
    for subscription in autoplay_subscriptions:
        subscription.unsubscribe()
    autoplay_subscriptions.clear()

    # Unsubscribe from all reporter event subscriptions
    for subscription in reporter_subscriptions:
        subscription.unsubscribe()
    reporter_subscriptions.clear()

    # Clean up the QobuzReporter using its shutdown method
    if reporter is not None:
        reporter.shutdown()
        reporter = None

    # Clean up the QobuzAutoplay module
    if autoplay is not None:
        autoplay = None

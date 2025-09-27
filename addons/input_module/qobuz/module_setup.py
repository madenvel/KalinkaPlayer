from addons.input_module.qobuz.qobuz_reporter import QobuzReporter
from sdk.api import PlayQueueAPI, PluginContext
from addons.input_module.qobuz.qobuz_autoplay import QobuzAutoplay
from addons.input_module.qobuz import (
    QobuzInputModule,
    get_client,
)

from sdk.inputmodule import InputModule
from sdk.api import PlayQueueAPI, EventListenerAPI
from sdk.events import EventType
from addons.input_module.qobuz.config_model import QobuzConfig

autoplay = None
reporter = None
# Store subscriptions for cleanup
autoplay_subscriptions = []
reporter_subscriptions = []

Config = QobuzConfig


def setup_autoplay(
    client,
    playqueue: PlayQueueAPI,
    track_browser: InputModule,
    event_listener: EventListenerAPI,
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
    event_listener: EventListenerAPI,
):
    global reporter, reporter_subscriptions

    reporter = QobuzReporter(client)
    reporter_subscriptions.append(
        event_listener.subscribe(EventType.StateChanged, reporter.on_state_changed)
    )


def setup(
    config: QobuzConfig,
    context: PluginContext,
):
    client = get_client(config)
    inputmodule = QobuzInputModule(config, client, context.event_emitter)
    setup_autoplay(client, context.playqueue, inputmodule, context.listener)
    setup_reporter(client, context.listener)

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

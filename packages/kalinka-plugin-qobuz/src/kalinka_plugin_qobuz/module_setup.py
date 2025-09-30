from kalinka_plugin_sdk.api import (
    EventListenerAPI,
    PlayQueueAPI,
    PluginContext,
)  # runtime Protocols
from kalinka_plugin_sdk.events import EventType
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import QobuzConfig
from .qobuz_autoplay import QobuzAutoplay
from .qobuz_reporter import QobuzReporter
from .qobuz import QobuzInputModule, get_client


REQUIRES_SDK = ">=1.0,<2"
PLUGIN_ID = "qobuz"

Config = QobuzConfig

autoplay = None
reporter = None
# Store subscriptions for cleanup
autoplay_subscriptions = []
reporter_subscriptions = []


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
) -> InputModule:
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

from typing import Optional
from kalinka_plugin_sdk.api import (
    EventListenerAPI,
    InputModulePlugin,
    PlayQueueAPI,
    PluginContext,
)
from kalinka_plugin_sdk.events import EventType
from kalinka_plugin_sdk.inputmodule import InputModule

from .config_model import QobuzConfig
from .qobuz_autoplay import QobuzAutoplay
from .qobuz_reporter import QobuzReporter
from .qobuz import QobuzInputModule, get_client


class KalinkaPluginQobuz(InputModulePlugin):
    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "qobuz"
    CONFIG_MODEL = QobuzConfig

    def __init__(self):
        self.autoplay_subscriptions = []
        self.reporter_subscriptions = []
        self.autoplay = None
        self.reporter = None
        self.interface = None

    def module_name(self) -> str:
        return "Qobuz Input Module"

    def get_interface(self) -> Optional[InputModule]:
        return self.interface

    def _setup_autoplay(
        self,
        client,
        playqueue: PlayQueueAPI,
        track_browser: InputModule,
        event_listener: EventListenerAPI,
    ):
        self.autoplay = QobuzAutoplay(client, playqueue, track_browser)
        self.autoplay_subscriptions.append(
            event_listener.subscribe(
                EventType.RequestMoreTracks, self.autoplay.add_recommendation
            )
        )
        self.autoplay_subscriptions.append(
            event_listener.subscribe(EventType.TracksAdded, self.autoplay.add_tracks)
        )
        self.autoplay_subscriptions.append(
            event_listener.subscribe(
                EventType.TracksRemoved, self.autoplay.remove_tracks
            )
        )

    def _setup_reporter(
        self,
        client,
        event_listener: EventListenerAPI,
    ):

        self.reporter = QobuzReporter(client)
        self.reporter_subscriptions.append(
            event_listener.subscribe(
                EventType.StateChanged, self.reporter.on_state_changed
            )
        )

    def setup(
        self,
        context: PluginContext,
    ) -> None:
        config = QobuzConfig(**context.config.model_dump())
        client = get_client(config)
        self.interface = QobuzInputModule(config, client, context.event_emitter)
        self._setup_autoplay(
            client, context.playqueue, self.interface, context.listener
        )
        self._setup_reporter(client, context.listener)

    def shutdown(self):

        # Unsubscribe from all autoplay event subscriptions
        for subscription in self.autoplay_subscriptions:
            subscription.unsubscribe()
        self.autoplay_subscriptions.clear()

        # Unsubscribe from all reporter event subscriptions
        for subscription in self.reporter_subscriptions:
            subscription.unsubscribe()
        self.reporter_subscriptions.clear()

        # Clean up the QobuzReporter using its shutdown method
        if self.reporter is not None:
            self.reporter.shutdown()
            self.reporter = None

        # Clean up the QobuzAutoplay module
        if self.autoplay is not None:
            self.autoplay = None

from typing import Optional
from kalinka_plugin_sdk.api import (
    PluginContext,
    OutputDevicePlugin,
)
from kalinka_plugin_sdk.events import EventType
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .config_model import KalinkaPluginMusiccastConfig
from .musiccast import KalinkaPluginMusiccastDevice


class KalinkaPluginMusiccast(OutputDevicePlugin):
    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "musiccast"
    CONFIG_MODEL = KalinkaPluginMusiccastConfig

    def __init__(self):
        self._device = None  #
        self._device_subscriptions = []

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

    def setup(self, context: PluginContext) -> None:
        config = KalinkaPluginMusiccastConfig(**context.config.model_dump())
        self._device = KalinkaPluginMusiccastDevice(
            config, context.playqueue, context.event_emitter
        )
        self._device_subscriptions.append(
            context.listener.subscribe(
                EventType.StateChanged, self._device._on_state_changed
            )
        )

    def shutdown(self) -> None:
        for subscription in self._device_subscriptions:
            subscription.unsubscribe()

        self._device_subscriptions.clear()
        self._device = None

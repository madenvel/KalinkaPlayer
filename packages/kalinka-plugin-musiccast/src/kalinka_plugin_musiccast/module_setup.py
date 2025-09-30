from kalinka_plugin_sdk.api import PluginContext  # runtime Protocols
from kalinka_plugin_sdk.events import EventType
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .config_model import KalinkaPluginMusiccastConfig
from .musiccast import KalinkaPluginMusiccastDevice

device = None
device_subscriptions = []

Config = KalinkaPluginMusiccastConfig

REQUIRES_SDK = ">=1.0,<2"
PLUGIN_ID = "musiccast"


def setup(
    config: KalinkaPluginMusiccastConfig, context: PluginContext
) -> ExternalOutputDevice:
    global device
    device = KalinkaPluginMusiccastDevice(
        config, context.playqueue, context.event_emitter
    )
    device_subscriptions.append(
        context.listener.subscribe(EventType.StateChanged, device._on_state_changed)
    )

    return device


def shutdown():
    global device_subscriptions, device
    for subscription in device_subscriptions:
        subscription.unsubscribe()

    device_subscriptions.clear()
    device = None

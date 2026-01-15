"""
Kalinka Plugin SDK

A Software Development Kit for developing input modules and device plugins for the Kalinka Player.
"""

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "1.0.0"

from .api import (
    PlayQueueController,
    EventEmitter,
    EventListener,
    LoggerAPI,
    API_VERSION,
)
from .events import (
    PlayQueueEventType,
    PlayQueueEvent,
    PlayQueueState,
    PlaybackStateChangedEvent,
    RequestMoreTracksEvent,
    TracksAddedEvent,
    TracksRemovedEvent,
    PlaybackModeChangedEvent,
    PlaybackErrorEvent,
)

from .datamodel import (
    EntityType,
    EntityId,
    DeviceVolume,
)
from .inputmodule import (
    InputModule,
    TrackUrl,
    TrackInfo,
    SearchType,
)
from .ext_device import (
    ExternalOutputDevice,
    SupportedFunction,
)
from .plugin import (
    InputPluginContext,
    OutputDevicePluginContext,
)
from .module_config import ModuleConfig

__all__ = [
    "API_VERSION",
    "__version__",
    # APIs
    "PlayQueueController",
    "EventEmitter",
    "EventListener",
    "LoggerAPI",
    "InputPluginContext",
    "OutputDevicePluginContext",
    # Events and States
    "PlayQueueEventType",
    "PlayQueueEvent",
    "PlayQueueState",
    "PlaybackStateChangedEvent",
    "RequestMoreTracksEvent",
    "TracksAddedEvent",
    "TracksRemovedEvent",
    "PlaybackModeChangedEvent",
    "PlaybackErrorEvent",
    # Data Models
    "EntityType",
    "EntityId",
    "DeviceVolume",
    "TrackUrl",
    "TrackInfo",
    "SearchType",
    # Base Classes
    "InputModule",
    "ExternalOutputDevice",
    "SupportedFunction",
    "ModuleConfig",
]

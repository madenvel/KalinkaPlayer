"""
Kalinka Plugin SDK

A Software Development Kit for developing input modules and device plugins for the Kalinka Player.
"""

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "1.0.0"

from .api import (
    PlayQueueAPI,
    EventEmitterAPI,
    EventListenerAPI,
    LoggerAPI,
    PluginContext,
    API_VERSION,
)
from .events import (
    EventType,
    FavoriteAddedEvent,
    FavoriteRemovedEvent,
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
from .module_config import ModuleConfig

__all__ = [
    "API_VERSION",
    "__version__",
    # APIs
    "PlayQueueAPI",
    "EventEmitterAPI",
    "EventListenerAPI",
    "LoggerAPI",
    "PluginContext",
    # Events and States
    "EventType",
    "FavoriteAddedEvent",
    "FavoriteRemovedEvent",
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

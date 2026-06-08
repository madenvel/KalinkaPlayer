"""
Kalinka Plugin SDK

A Software Development Kit for developing input modules and device plugins for the Kalinka Player.
"""

# Fixed during pre-1.0 development; kept in sync with [project].version in pyproject.toml.
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
    TrackMovedEvent,
    TrackUnavailableEvent,
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
from .module_health import ModuleHealthState, ModuleState
from .dynamic_fields import DynamicFieldDecl
from .optional_packages import OptionalPackageSpec

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
    "TrackMovedEvent",
    "TrackUnavailableEvent",
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
    "ModuleHealthState",
    "ModuleState",
    "DynamicFieldDecl",
    "OptionalPackageSpec",
]

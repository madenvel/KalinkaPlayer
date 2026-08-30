"""
Kalinka Plugin SDK

A Software Development Kit for developing input modules and device plugins for the Kalinka Player.
"""

from ._version import __version__

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
    RenderersChangedEvent,
    CurrentRendererChangedEvent,
    RendererDescriptor,
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
from .embedding import TextEmbedder
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
    "TextEmbedder",
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
    "RenderersChangedEvent",
    "CurrentRendererChangedEvent",
    "RendererDescriptor",
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

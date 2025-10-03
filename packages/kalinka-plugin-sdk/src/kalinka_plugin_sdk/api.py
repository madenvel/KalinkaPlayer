# kalinka_plugin_sdk/api.py
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, Optional, TypeVar, Generic, Union
from enum import Enum

from kalinka_plugin_sdk.datamodel import PlaybackMode, PlayerState, Track, TrackList
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.module_config import ModuleConfig

from .inputmodule import InputModule, TrackInfo
from .events import AnyEventPayload, EventType

API_VERSION = "1.0"


class PluginType(Enum):
    """Enumeration of plugin types."""

    INPUT_MODULE = "input_module"
    OUTPUT_DEVICE = "output_device"
    EVENT_LISTENER = "event_listener"


class PluginException(Exception):
    """Base exception for plugin-related errors."""

    pass


class PluginSetupException(PluginException):
    """Raised when plugin setup fails."""

    pass


class PluginInterfaceException(PluginException):
    """Raised when plugin interface is invalid."""

    pass


class PlayQueueAPI(Protocol):
    """
    Contract for play queue operations.
    All methods should be implemented by the play queue provider.
    """

    def play(self, index: Optional[int] = None) -> None:
        """Start playback at the given index, or resume if index is None."""
        ...

    def play_next(self, index: int) -> None:
        """Play the track at the given index next."""
        ...

    def pause(self, paused: bool) -> None:
        """Pause or resume playback.
        If paused is True, pause playback; if False, resume playback.
        """
        ...

    def next(self) -> None:
        """Skip to the next track."""
        ...

    def prev(self) -> None:
        """Go back to the previous track."""
        ...

    def seek(self, position_ms: int) -> None:
        """Seek to the given position in milliseconds."""
        ...

    def stop(self) -> None:
        """Stop playback."""
        ...

    def add(self, tracks: list[TrackInfo]) -> None:
        """Add tracks to the queue."""
        ...

    def remove(self, tracks: list[int]) -> None:
        """Remove tracks by their indices."""
        ...

    def list(self, offset: int, limit: int) -> TrackList:
        """List tracks in the queue with pagination."""
        ...

    def get_track_info(self, index: int) -> Optional[Track]:
        """Get info for the track at the given index."""
        ...

    def get_state(self) -> PlayerState:
        """Get the current playback state."""
        ...

    def clear(self) -> None:
        """Clear the play queue.
        Stops the playback if it is active.
        """
        ...

    def set_playback_mode(
        self,
        shuffle: Optional[bool],
        repeat_single: Optional[bool],
        repeat_all: Optional[bool],
    ) -> None:
        """Set playback modes: shuffle, repeat single, repeat all."""
        ...

    def get_playback_mode(self) -> PlaybackMode:
        """Get the current playback mode."""
        ...


class EventEmitterAPI(Protocol):
    def dispatch(self, event: AnyEventPayload) -> None: ...


class SubscriptionHandle(Protocol):
    def unsubscribe(self) -> None: ...


class EventListenerAPI(Protocol):
    def subscribe(
        self, topic: EventType, handler: Callable[[AnyEventPayload], None]
    ) -> SubscriptionHandle: ...


class LoggerAPI(Protocol):
    def debug(self, msg, *args, **kwargs): ...
    def info(self, msg, *args, **kwargs): ...
    def warning(self, msg, *args, **kwargs): ...
    def error(self, msg, *args, **kwargs): ...
    def critical(self, msg, *args, **kwargs): ...
    def fatal(self, msg, *args, **kwargs): ...


# Type variable for plugin interfaces
T = TypeVar("T", InputModule, ExternalOutputDevice, None)


@dataclass
class PluginContext:
    playqueue: PlayQueueAPI
    event_emitter: EventEmitterAPI
    listener: EventListenerAPI
    logger: LoggerAPI
    plugin_id: str
    sdk_version: str  # equals API_VERSION
    capabilities: set[str]
    config: ModuleConfig


class PluginBase(ABC):
    PLUGIN_ID: str
    REQUIRES_SDK: str
    PLUGIN_TYPE: PluginType
    CONFIG_MODEL: type[ModuleConfig]

    @abstractmethod
    def setup(self, context: PluginContext) -> None:
        """Called when the plugin is being loaded. Initialize resources here.

        Raises:
            PluginSetupException: If setup fails
        """
        ...

    def shutdown(self) -> None:
        """Called when the plugin is being unloaded. Clean up resources here.
        Should not raise exceptions - log errors instead.
        """
        pass


class TypedPluginBase(PluginBase, Generic[T]):
    """Base class for plugins with typed interfaces."""

    def get_interface(self) -> Optional[T]:
        """Return the plugin's specific interface if applicable."""
        return None


class InputModulePlugin(TypedPluginBase[InputModule]):
    """Base class for input module plugins."""

    PLUGIN_TYPE = PluginType.INPUT_MODULE

    @abstractmethod
    def get_interface(self) -> Optional[InputModule]:
        """Return the plugin's specific interface if applicable."""
        ...


class OutputDevicePlugin(TypedPluginBase[ExternalOutputDevice]):
    """Base class for output device plugins."""

    PLUGIN_TYPE = PluginType.OUTPUT_DEVICE

    @abstractmethod
    def get_interface(self) -> Optional[ExternalOutputDevice]:
        """Return the plugin's specific interface if applicable."""
        ...


class EventListenerPlugin(TypedPluginBase[None]):
    """Base class for event listener plugins."""

    PLUGIN_TYPE = PluginType.EVENT_LISTENER


# def get_plugin_type(plugin: PluginBase) -> PluginType:
#     """Get the plugin type safely."""
#     return plugin.PLUGIN_TYPE


def validate_plugin_consistency(plugin: PluginBase) -> bool:
    """Validate that plugin type matches its interface capabilities."""
    plugin_type = plugin.PLUGIN_TYPE

    if plugin_type == PluginType.INPUT_MODULE:
        return isinstance(plugin, InputModulePlugin)
    elif plugin_type == PluginType.OUTPUT_DEVICE:
        return isinstance(plugin, OutputDevicePlugin)
    elif plugin_type == PluginType.EVENT_LISTENER:
        return isinstance(plugin, EventListenerPlugin)

    return False


def cast_plugin_interface(
    plugin: PluginBase,
) -> Union[InputModule, ExternalOutputDevice, None]:
    """Safely cast plugin to its interface type."""
    if isinstance(plugin, (InputModulePlugin, OutputDevicePlugin)):
        interface = plugin.get_interface()
        if validate_plugin_consistency(plugin):
            return interface
    return None

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Generic, Optional, TypeVar
from .api import EventEmitter, EventListener, LoggerAPI, PlayQueueController
from .dynamic_fields import DynamicFieldDecl
from .events import PlayQueueEventType, PlayQueueEvent, PlayQueueState
from .ext_device_events import ExtDeviceEventType, ExtDeviceEvent, ExtDeviceState
from .module_config import ModuleConfig
from .module_health import ModuleHealthState, ModuleState
from .inputmodule import InputModule
from .ext_device import ExternalOutputDevice


class PluginType(Enum):
    """Enumeration of plugin types."""

    INPUT_MODULE = "input_module"
    OUTPUT_DEVICE = "output_device"


class PluginException(Exception):
    """Base exception for plugin-related errors."""

    pass


class PluginSetupException(PluginException):
    """Raised when plugin setup fails."""

    pass


class PluginInterfaceException(PluginException):
    """Raised when plugin interface is invalid."""

    pass


# Type variable for plugin interfaces
T = TypeVar("T", InputModule, ExternalOutputDevice, None)


@dataclass
class PluginContextBase:
    logger: LoggerAPI
    plugin_id: str
    sdk_version: str  # equals API_VERSION
    config: ModuleConfig
    listener: EventListener[PlayQueueEventType, PlayQueueEvent, PlayQueueState]


CTX_TYPE = TypeVar("CTX_TYPE", bound=PluginContextBase)
PLUGIN_CLASS = TypeVar("PLUGIN_CLASS", bound=InputModule | ExternalOutputDevice)


class PluginBase(ABC, Generic[PLUGIN_CLASS, CTX_TYPE]):
    PLUGIN_ID: str
    REQUIRES_SDK: str
    PLUGIN_TYPE: PluginType
    CONFIG_MODEL: type[ModuleConfig]

    # Optional: plugins with internal sub-features can declare dynamic
    # fields here. The server reads this at load time to build the
    # presentation schema and the resolver registry. The default is empty.
    DYNAMIC_FIELDS: ClassVar[dict[str, DynamicFieldDecl]] = {}

    @abstractmethod
    async def setup(self, context: CTX_TYPE) -> None:
        """Called when the plugin is being loaded. Initialize resources here.

        Raises:
            PluginSetupException: If setup fails
        """
        ...

    async def shutdown(self) -> None:
        """Called when the plugin is being unloaded. Clean up resources here.
        Should not raise exceptions - log errors instead.
        """
        pass

    def get_interface(self) -> Optional[PLUGIN_CLASS]:
        """Return the plugin's specific interface if applicable."""
        return None

    async def get_state(self) -> ModuleState:
        """Return the plugin's current health roll-up.

        Called on demand by the server. The plugin is responsible for
        keeping enough internal bookkeeping (sub-feature states, optional
        package availability) to answer cheaply — this should not perform
        I/O on the hot path.

        Default implementation reports READY with no message. Plugins
        with internal sub-features should override.
        """
        return ModuleState(state=ModuleHealthState.READY)

    async def resolve_dynamic_field(self, path: str) -> Any:
        """Resolve the current value of a dynamic field declared via
        ``DYNAMIC_FIELDS``.

        ``path`` is the key from ``DYNAMIC_FIELDS`` (e.g. "searcher.status").
        Raises ``KeyError`` if the path is unknown; the server treats this
        as a 404 for the caller.
        """
        raise KeyError(path)


@dataclass
class InputPluginContext(PluginContextBase):
    """Context provided to input module plugins."""

    playqueue: PlayQueueController


@dataclass
class OutputDevicePluginContext(PluginContextBase):
    """Context provided to output device plugins."""

    emitter: EventEmitter[ExtDeviceEvent, ExtDeviceState]


@dataclass
class InputModulePlugin(PluginBase[InputModule, InputPluginContext]):
    """Base class for input module plugins."""

    PLUGIN_TYPE = PluginType.INPUT_MODULE


@dataclass
class OutputDevicePlugin(PluginBase[ExternalOutputDevice, OutputDevicePluginContext]):
    """Base class for output device plugins."""

    PLUGIN_TYPE = PluginType.OUTPUT_DEVICE


def validate_plugin_consistency(plugin: PluginBase) -> bool:
    """Validate that plugin type matches its interface capabilities."""
    plugin_type = plugin.PLUGIN_TYPE

    if plugin_type == PluginType.INPUT_MODULE:
        return isinstance(plugin, InputModulePlugin)
    elif plugin_type == PluginType.OUTPUT_DEVICE:
        return isinstance(plugin, OutputDevicePlugin)

    return False


def cast_plugin_interface(
    plugin: PluginBase,
) -> InputModule | ExternalOutputDevice | None:
    """Safely cast plugin to its interface type."""
    if isinstance(plugin, (InputModulePlugin, OutputDevicePlugin)):
        interface = plugin.get_interface()
        if validate_plugin_consistency(plugin):
            return interface
    return None

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Generic, Optional, TypeVar
from .api import EventEmitter, EventListener, LoggerAPI, PlayQueueController
from .dynamic_fields import DynamicFieldDecl
from .embedding import TextEmbedder
from .events import PlayQueueEventType, PlayQueueEvent, PlayQueueState
from .ext_device_events import ExtDeviceEventType, ExtDeviceEvent, ExtDeviceState
from .module_config import ModuleConfig
from .module_health import ModuleHealthState, ModuleState
from .optional_packages import OptionalPackageSpec
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
    sdk_version: str  # the SDK distribution version, kalinka_plugin_sdk.__version__
    config: ModuleConfig
    listener: EventListener[PlayQueueEventType, PlayQueueEvent, PlayQueueState]
    # Server-owned shared text embedder (SDK 1.2+). One model instance serves
    # every plugin and the server itself — plugins must not load their own.
    # kw_only so this defaulted field can live on the base without breaking
    # subclasses that declare required positional fields.
    embedder: Optional[TextEmbedder] = field(default=None, kw_only=True)


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

    # Optional: pip packages the plugin installs on demand rather than
    # depending on outright, keyed by the token install requests use. The
    # server reads this at load time to build the global allow-list, and
    # the deb build exports it to a root-owned manifest. The default is
    # empty — a plugin whose dependencies are all hard needs no entry.
    OPTIONAL_PACKAGES: ClassVar[dict[str, OptionalPackageSpec]] = {}

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

    async def required_packages(self) -> list[str]:
        """Return optional-package keys the plugin would need to install
        given its *current* in-memory config.

        Called by the server on restart to auto-queue installs when a
        user enables a sub-feature whose packages aren't yet importable.
        Keys must come from this plugin's ``OPTIONAL_PACKAGES`` (or
        another loaded plugin's — the server validates against the
        global registry). The default returns ``[]``: thin plugins
        without optional deps need not implement this.

        The plugin reads its *current* config (typically via the context
        captured at setup time), not the config at last setup, so this
        reflects any changes the user has just staged through
        ``PUT /server/config``.
        """
        return []


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

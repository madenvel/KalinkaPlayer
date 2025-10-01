import json
import logging
import os
import types
from enum import Enum
from importlib.metadata import entry_points
from importlib import import_module
from queue import Queue
from typing import Generator, get_type_hints

from kalinka_plugin_sdk.api import (
    PluginContext,
)
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk import API_VERSION
from kalinka_server.plugin_event_queue import PluginEventQueue

from .async_common import EventEmitter, EventListener
from .config_model import KalinkaConfig
from .playqueue import PlayQueue
from .plugin_api import (
    EventEmitterAPIImpl,
    PlayQueueAPIImpl,
)

logger = logging.getLogger(__name__.split(".")[-1])


class PluginType(str, Enum):
    INPUT_MODULE = "input_module"
    DEVICE = "device"


class ModuleHealthState(str, Enum):
    """Enum to represent the health state of a module."""

    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"


class PreparedModule:
    """A class to hold prepared modules for shutdown."""

    def __init__(self, module: types.ModuleType, config: ModuleConfig):
        self.module = module
        self.config = config
        self.interface: ExternalOutputDevice | InputModule | None = None
        self.health_state: ModuleHealthState = ModuleHealthState.DISABLED
        self.error_message: str | None = None
        self.plugin_type: PluginType | None = None

    def setup(self, plugin_context: PluginContext):
        """Setup the module with the provided components."""
        if hasattr(self.module, "setup"):
            if self.config.enabled is False:
                logger.info(f"Skipping disabled module: {self.config.name}")
                return

            interface = self.module.setup(self.config, plugin_context)
            if isinstance(interface, ExternalOutputDevice) or isinstance(
                interface, InputModule
            ):
                self.interface = interface
                self.plugin_type = (
                    PluginType.DEVICE
                    if isinstance(interface, ExternalOutputDevice)
                    else PluginType.INPUT_MODULE
                )
                logger.info(f"Module {self.config.name} setup successfully.")
            else:
                raise TypeError(
                    f"Module {self.config.name} setup must return an instance of ExternalOutputDevice or InputModule."
                )
        else:
            raise AttributeError(f"Module {self.config.name} has no setup method.")

    def setup_as_disabled(self):
        """Setup the module as disabled."""
        self.health_state = ModuleHealthState.DISABLED
        self.interface = None
        type_hints = get_type_hints(self.module.setup)
        return_type = type_hints.get("return", None)
        if return_type not in [ExternalOutputDevice, InputModule]:
            raise TypeError(
                f"Module {self.config.name} setup must return an instance of ExternalOutputDevice or InputModule."
            )

        self.plugin_type = (
            PluginType.DEVICE
            if return_type is ExternalOutputDevice
            else PluginType.INPUT_MODULE
        )

    def shutdown(self):
        """Shutdown the module if it has a shutdown method."""
        if hasattr(self.module, "shutdown"):
            self.module.shutdown()
            self.interface = None
        else:
            logger.warning(f"Module {self.config.name} has no shutdown method.")

    def update_config(self, config_json: dict):
        """Update the module's configuration."""
        if hasattr(self.module, "Config"):
            self.config = self.module.Config(**config_json)
        else:
            raise AttributeError(f"Module {self.module.__name__} has no Config class.")


class PreparedModuleCollection:
    """A collection to hold prepared input modules and devices."""

    def __init__(self):
        self.prepared_input_modules: dict[str, PreparedModule] = {}
        self.prepared_devices: dict[str, PreparedModule] = {}
        self.enabled_input_modules: set[str] = set()
        self.enabled_devices: set[str] = set()

    def update_enabled_input_modules(self):
        """Update the set of enabled input module names."""
        self.enabled_input_modules = {
            name
            for name, module in self.prepared_input_modules.items()
            if module.config.enabled
        }

    def update_enabled_devices(self):
        """Update the set of enabled device names."""
        self.enabled_devices = {
            name
            for name, module in self.prepared_devices.items()
            if module.config.enabled
        }


modules = PreparedModuleCollection()


def scan_entry_points() -> Generator[tuple[str, types.ModuleType], None, None]:
    """Scan for installed plugins using entry points and yield modules with their type."""
    try:
        eps = entry_points(group="kalinka.plugins")
    except Exception as e:
        logger.warning(f"Failed to load entry points: {e}")
        return

    for ep in eps:
        try:
            logger.debug(f"Loading entry point: {ep.name}")

            # Load the module containing the setup function
            module = ep.load()

            # Get the module where the setup function is defined
            setup_module = module.__module__
            if hasattr(module, "__module__"):
                actual_module = import_module(setup_module)
            else:
                actual_module = module

            # Check if the module has required attributes
            if (
                hasattr(actual_module, "setup")
                and hasattr(actual_module, "Config")
                and hasattr(actual_module, "shutdown")
                and hasattr(actual_module, "PLUGIN_ID")
            ):
                logger.info("Found plugin module: %s", actual_module.PLUGIN_ID)
                yield (actual_module.PLUGIN_ID, actual_module)

            else:
                logger.warning(
                    f"Plugin {ep.name} is missing required attributes: setup, shutdown, Config, PLUGIN_TYPE or PLUGIN_ID"
                )

        except Exception as e:
            logger.error(f"Failed to load plugin {ep.name}: {e}", exc_info=True)


def read_or_create_module_config(
    config_path: str, module_name: str, module: types.ModuleType
):
    """Read or create a module configuration file.
    If the file does not exist, it will be created with the default configuration.
    """
    config_file = os.path.join(config_path, f"{module_name}_config.cfg")

    config_data: dict | None = None

    try:
        with open(config_file, "r") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        logger.warning(
            f"Config file not found for {module_name}, creating default config."
        )

    if config_data is None:
        logger.info(f"Creating default config for {module_name} at {config_file}")
        return module.Config()

    return module.Config(**config_data)


def scan_and_setup_plugins_from_entry_points(
    config_path: str,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
) -> Generator[tuple[str, PreparedModule], None, None]:
    """Scan for installed plugins using entry points and setup those matching the specified type."""

    for name, module in scan_entry_points():

        logger.info(f"Found plugin: {name}")
        prepared_module = None
        try:
            config = read_or_create_module_config(config_path, name, module)
            prepared_module = PreparedModule(module, config)
            if config.enabled:
                plugin_context = make_plugin_context(
                    name, playqueue, event_emitter, event_listener
                )
                prepared_module.setup(plugin_context)
                prepared_module.health_state = ModuleHealthState.READY
            else:
                logger.info(f"Plugin {name} is disabled in configuration.")
                prepared_module.setup_as_disabled()

        except Exception as e:
            logger.error(f"Failed to setup plugin {name}: {e}")
            if prepared_module is not None:
                prepared_module.health_state = ModuleHealthState.ERROR
                prepared_module.error_message = str(e)

        if prepared_module is not None:
            yield name, prepared_module


def make_plugin_context(
    name: str,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
) -> PluginContext:
    """Create a PluginContext instance."""
    context = PluginContext()
    context.playqueue = PlayQueueAPIImpl(playqueue)
    context.event_emitter = EventEmitterAPIImpl(event_emitter)
    context.listener = PluginEventQueue(event_listener)
    context.logger = logging.getLogger(name)
    context.plugin_id = name
    context.sdk_version = API_VERSION
    context.capabilities = set()
    return context


def scan_and_setup_plugins(
    config_path: str,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
):
    """Scan for input modules from both entry points and legacy filesystem locations."""

    # First, scan for input modules using entry points
    input_modules = {}
    devices = {}
    for name, module in scan_and_setup_plugins_from_entry_points(
        config_path, playqueue, event_emitter, event_listener
    ):
        plugin_type = module.plugin_type
        if plugin_type == PluginType.INPUT_MODULE:
            input_modules[name] = module
        elif plugin_type == PluginType.DEVICE:
            devices[name] = module

    modules.prepared_input_modules = {**input_modules}
    modules.update_enabled_input_modules()

    modules.prepared_devices = {**devices}
    modules.update_enabled_devices()


def setup(config_path: str, config: KalinkaConfig) -> tuple[PlayQueue, EventListener]:
    """
    Setup the player components.

    Args:
        config_path: Path to the configuration file

    Returns:
        tuple: (playqueue, event_listener)
    """

    # Create core components
    queue = Queue()
    event_emitter = EventEmitter(queue)
    event_listener = EventListener(queue)
    playqueue = PlayQueue(config, event_emitter)

    # Scan and setup plugins
    scan_and_setup_plugins(config_path, playqueue, event_emitter, event_listener)

    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    logger.info("Output devices found: %s", list(modules.prepared_devices.keys()))

    return playqueue, event_listener


def shutdown_modules(modules: dict[str, PreparedModule], config_path: str):
    """Shutdown all modules and save their configurations."""
    for module_name, prepared_module in modules.items():
        logger.info(f"Shutting down module: {module_name}")
        if hasattr(prepared_module.module, "shutdown"):
            try:
                prepared_module.module.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down module {module_name}: {e}")

        config_file_path = os.path.join(config_path, f"{module_name}_config.cfg")
        # Ensure the directory exists
        config_dir = os.path.dirname(config_file_path)
        if config_dir:  # Only create directory if path is not empty
            os.makedirs(config_dir, exist_ok=True)

        with open(config_file_path, "w") as f:
            json.dump(prepared_module.config.model_dump(), f, indent=2)
            logger.info(f"Saved config for module {module_name} to {config_file_path}")


def shutdown(config_path: str):
    global prepared_input_modules, prepared_devices

    shutdown_modules(modules.prepared_input_modules, config_path)
    shutdown_modules(modules.prepared_devices, config_path)

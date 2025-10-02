import json
import logging
import os
from enum import Enum
from importlib.metadata import entry_points
from queue import Queue
from typing import Generator
from kalinka_plugin_sdk.api import (
    PluginContext,
    PluginType,
    cast_plugin_interface,
)
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk import API_VERSION
from kalinka_server.plugin_event_queue import PluginEventQueue

from .async_common import EventEmitter, EventListener
from .config_model import KalinkaConfig
from .playqueue import PlayQueue
from kalinka_plugin_sdk.api import PluginBase
from .plugin_api import (
    EventEmitterAPIImpl,
    PlayQueueAPIImpl,
)

logger = logging.getLogger(__name__.split(".")[-1])


class ModuleHealthState(str, Enum):
    """Enum to represent the health state of a module."""

    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"


class PreparedPlugin:
    """A class to hold prepared plugins for shutdown."""

    def __init__(self, plugin_class: type[PluginBase]):
        self.plugin_class = plugin_class
        self.plugin_instance = None
        self.health_state = ModuleHealthState.DISABLED
        self.error_message: str | None = None

    def setup(self, plugin_context: PluginContext):
        """Setup the module with the provided components."""
        self.plugin_instance = self.plugin_class()
        self.plugin_context = plugin_context
        self.plugin_instance.setup(plugin_context)
        self.health_state = ModuleHealthState.READY
        self.interface = cast_plugin_interface(self.plugin_instance)

    def shutdown(self):
        """Shutdown the module if it has a shutdown method."""
        if self.plugin_instance:
            self.plugin_instance.shutdown()


class PreparedModuleCollection:
    """A collection to hold prepared input modules and devices."""

    def __init__(self):
        self.prepared_input_modules: dict[str, PreparedPlugin] = {}
        self.prepared_devices: dict[str, PreparedPlugin] = {}
        self.enabled_input_modules: set[str] = set()
        self.enabled_devices: set[str] = set()

    def update_enabled_input_modules(self):
        """Update the set of enabled input module names."""
        self.enabled_input_modules = {
            name
            for name, module in self.prepared_input_modules.items()
            if module.health_state == ModuleHealthState.READY
        }

    def update_enabled_devices(self):
        """Update the set of enabled device names."""
        self.enabled_devices = {
            name
            for name, module in self.prepared_devices.items()
            if module.health_state == ModuleHealthState.READY
        }


modules = PreparedModuleCollection()


def scan_entry_points() -> Generator[tuple[str, type], None, None]:
    """Scan for installed plugins using entry points and yield classes derived from PluginBase."""

    try:
        eps = entry_points(group="kalinka.plugins")
    except Exception as e:
        logger.warning(f"Failed to load entry points: {e}")
        return

    for ep in eps:
        try:
            logger.debug(f"Loading entry point: {ep.name}")

            # Load the class from the entry point
            plugin_cls = ep.load()

            # Check if it's a subclass of PluginBase
            if isinstance(plugin_cls, type) and issubclass(plugin_cls, PluginBase):
                # Check for required attributes
                if hasattr(plugin_cls, "PLUGIN_ID"):
                    logger.info("Found plugin class: %s", plugin_cls.PLUGIN_ID)
                    yield (plugin_cls.PLUGIN_ID, plugin_cls)
                else:
                    logger.warning(
                        f"Plugin class {ep.name} is missing required attribute: PLUGIN_ID"
                    )
            else:
                logger.warning(f"Entry point {ep.name} is not a subclass of PluginBase")

        except Exception as e:
            logger.error(f"Failed to load plugin {ep.name}: {e}", exc_info=True)


def read_or_create_module_config(
    config_path: str, plugin_name: str, plugin_class: type[PluginBase]
):
    """Read or create a module configuration file.
    If the file does not exist, it will be created with the default configuration.
    """
    config_file = os.path.join(config_path, f"{plugin_name}_config.cfg")

    config_data: dict | None = None

    try:
        with open(config_file, "r") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        logger.warning(
            f"Config file not found for {plugin_name}, creating default config."
        )
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON config for {plugin_name}: {e}")

    if config_data is None:
        logger.info(f"Creating default config for {plugin_name} at {config_file}")
        return plugin_class.CONFIG_MODEL()

    return plugin_class.CONFIG_MODEL(**config_data)


def scan_and_setup_plugins_from_entry_points(
    config_path: str,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
) -> Generator[tuple[str, PreparedPlugin], None, None]:
    """Scan for installed plugins using entry points and setup those matching the specified type."""

    for plugin_name, plugin_class in scan_entry_points():

        logger.info(f"Found plugin: {plugin_name}")
        prepared_module = None
        try:
            config = read_or_create_module_config(
                config_path, plugin_name, plugin_class
            )
            logger.info(f"Loaded config for {plugin_name}: {config}")
            prepared_module = PreparedPlugin(plugin_class)
            if config.enabled:
                plugin_context = make_plugin_context(
                    plugin_name, playqueue, event_emitter, event_listener, config
                )
                prepared_module.setup(plugin_context)
                prepared_module.health_state = ModuleHealthState.READY
            else:
                logger.info(
                    f"Plugin {plugin_name} is disabled in configuration - skipping"
                )

        except Exception as e:
            logger.error(f"Failed to setup plugin {plugin_name}: {e}")
            if prepared_module is not None:
                prepared_module.health_state = ModuleHealthState.ERROR
                prepared_module.error_message = str(e)

        if prepared_module is not None:
            yield plugin_name, prepared_module


def make_plugin_context(
    name: str,
    playqueue: PlayQueue,
    event_emitter: EventEmitter,
    event_listener: EventListener,
    config: ModuleConfig,
) -> PluginContext:
    """Create a PluginContext instance."""
    return PluginContext(
        playqueue=PlayQueueAPIImpl(playqueue),
        event_emitter=EventEmitterAPIImpl(event_emitter),
        listener=PluginEventQueue(event_listener),
        logger=logging.getLogger(name),
        plugin_id=name,
        sdk_version=API_VERSION,
        capabilities=set(),
        config=config,
    )


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
    for plugin_name, prepared_plugin in scan_and_setup_plugins_from_entry_points(
        config_path, playqueue, event_emitter, event_listener
    ):
        plugin_type = prepared_plugin.plugin_class.PLUGIN_TYPE
        if plugin_type == PluginType.INPUT_MODULE:
            input_modules[plugin_name] = prepared_plugin
        elif plugin_type == PluginType.OUTPUT_DEVICE:
            devices[plugin_name] = prepared_plugin

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


def shutdown_modules(modules: dict[str, PreparedPlugin], config_path: str):
    """Shutdown all modules and save their configurations."""
    for module_name, prepared_module in modules.items():
        logger.info(f"Shutting down module: {module_name}")
        if prepared_module.plugin_instance is None:
            logger.info(f"Module {module_name} was not initialized, skipping shutdown.")
            continue

        try:
            prepared_module.plugin_instance.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down module {module_name}: {e}")

        config_file_path = os.path.join(config_path, f"{module_name}_config.cfg")
        # Ensure the directory exists
        config_dir = os.path.dirname(config_file_path)
        if config_dir:  # Only create directory if path is not empty
            os.makedirs(config_dir, exist_ok=True)

        with open(config_file_path, "w") as f:
            json.dump(prepared_module.plugin_context.config.model_dump(), f, indent=2)
            logger.info(f"Saved config for module {module_name} to {config_file_path}")


def shutdown(config_path: str):
    global prepared_input_modules, prepared_devices

    shutdown_modules(modules.prepared_input_modules, config_path)
    shutdown_modules(modules.prepared_devices, config_path)

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from importlib.metadata import entry_points
from typing import AsyncGenerator, Generator

from kalinka_eventbus import EventBus
from kalinka_plugin_sdk import API_VERSION, DeviceVolume
from kalinka_plugin_sdk.datamodel import PlaybackMode, PlaybackState
from kalinka_plugin_sdk.events import (
    PlayQueueState,
    PlayQueueEvent,
    PlayQueueEventType,
)
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
)
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.plugin import (
    InputPluginContext,
    OutputDevicePluginContext,
    PluginBase,
    PluginType,
    cast_plugin_interface,
)
from pydantic import BaseModel, ConfigDict

from .config_model import KalinkaConfig
from .playqueue import PlayQueueImpl
from kalinka_plugin_sdk.api import PlayQueueController

logger = logging.getLogger(__name__.split(".")[-1])


@dataclass
class PlayerContext:
    playqueue: PlayQueueController
    playqueue_eventbus: EventBus[PlayQueueState, PlayQueueEventType, PlayQueueEvent]  # type: ignore[type-var]
    ext_device_eventbus: EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent]  # type: ignore[type-var]


class ModuleHealthState(str, Enum):
    """Enum to represent the health state of a module."""

    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class PreparedPlugin:
    """A class to hold prepared plugins for shutdown."""

    plugin_class: type[PluginBase]
    plugin_instance: PluginBase | None
    health_state: ModuleHealthState
    plugin_context: InputPluginContext | OutputDevicePluginContext
    interface: InputModule | ExternalOutputDevice | None
    error_message: str | None = None

    @classmethod
    async def setup(cls, 
              plugin_class: type[PluginBase],
              plugin_context: InputPluginContext | OutputDevicePluginContext):
        """Setup the module with the provided components."""
        plugin_instance = plugin_class()
        health_state = ModuleHealthState.DISABLED
        interface = None
        if plugin_context.config.enabled is True:
            await plugin_instance.setup(plugin_context)
            health_state = ModuleHealthState.READY
            interface = cast_plugin_interface(plugin_instance)
        else:
            logger.info(
                f"Plugin {plugin_class.PLUGIN_ID} is disabled in configuration - skipping setup"
            )

        return cls(
            plugin_class=plugin_class,
            plugin_instance=plugin_instance,
            health_state=health_state,
            plugin_context=plugin_context,
            interface=interface,
        )

    async def shutdown(self):
        """Shutdown the module if it has a shutdown method."""
        if self.plugin_instance:
            await self.plugin_instance.shutdown()


@dataclass
class PreparedModuleCollection:
    """A collection to hold prepared input modules and devices."""

    prepared_input_modules: dict[str, PreparedPlugin] = field(default_factory=dict)
    prepared_devices: dict[str, PreparedPlugin] = field(default_factory=dict)
    enabled_input_modules: set[str] = field(default_factory=set)
    enabled_devices: set[str] = field(default_factory=set)
    player_context: PlayerContext | None = None

    def _update_enabled_input_modules(self):
        """Update the set of enabled input module names."""
        self.enabled_input_modules = {
            name
            for name, module in self.prepared_input_modules.items()
            if module.health_state == ModuleHealthState.READY
        }

    def _update_enabled_devices(self):
        """Update the set of enabled device names."""
        self.enabled_devices = {
            name
            for name, module in self.prepared_devices.items()
            if module.health_state == ModuleHealthState.READY
        }

    def _scan_entry_points(self) -> Generator[tuple[str, type], None, None]:
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
                        logger.info("Found plugin class: %s, name: %s", plugin_cls.PLUGIN_TYPE, plugin_cls.PLUGIN_ID)
                        yield (plugin_cls.PLUGIN_ID, plugin_cls)
                    else:
                        logger.warning(
                            f"Plugin class {ep.name} is missing required attribute: PLUGIN_ID"
                        )
                else:
                    logger.warning(
                        f"Entry point {ep.name} is not a subclass of PluginBase"
                    )

            except Exception as e:
                logger.error(f"Failed to load plugin {ep.name}: {e}", exc_info=True)

    def _read_or_create_module_config(
        self, config_path: str, plugin_name: str, plugin_class: type[PluginBase]
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

    async def _scan_and_setup_plugins_from_entry_points(
        self,
        config_path: str,
    ) -> AsyncGenerator[tuple[str, PreparedPlugin], None]:
        """Scan for installed plugins using entry points and setup those matching the specified type."""

        for plugin_name, plugin_class in self._scan_entry_points():

            logger.info(f"Found plugin: {plugin_name}")
            prepared_module = None
            try:
                config = self._read_or_create_module_config(
                    config_path, plugin_name, plugin_class
                )
                plugin_context = self._make_plugin_context(
                    plugin_name, plugin_class, config
                )
                prepared_module = await PreparedPlugin.setup(plugin_class, plugin_context)

            except Exception as e:
                logger.error(f"Failed to setup plugin {plugin_name}: {e}")
                if prepared_module is not None:
                    prepared_module.health_state = ModuleHealthState.ERROR
                    prepared_module.error_message = str(e)

            if prepared_module is not None:
                yield plugin_name, prepared_module

    def _make_plugin_context(
        self,
        name: str,
        plugin_class: type[PluginBase],
        config: ModuleConfig,
    ) -> InputPluginContext | OutputDevicePluginContext:
        """Create a PluginContext instance."""
        if self.player_context is None:
            raise ValueError("PlayerContext is not set in PreparedModuleCollection")
        
        match plugin_class.PLUGIN_TYPE:
            case PluginType.INPUT_MODULE:
                return InputPluginContext(
                    playqueue=self.player_context.playqueue,
                    listener=self.player_context.playqueue_eventbus,  # type: ignore[arg-type]
                    logger=logging.getLogger(name),
                    plugin_id=name,
                    sdk_version=API_VERSION,
                    config=config,
                )
            case PluginType.OUTPUT_DEVICE:
                return OutputDevicePluginContext(
                    listener=self.player_context.playqueue_eventbus,  # type: ignore[arg-type]
                    emitter=self.player_context.ext_device_eventbus,  # type: ignore[arg-type]
                    logger=logging.getLogger(name),
                    plugin_id=name,
                    sdk_version=API_VERSION,
                    config=config,
                )
            case _:
                raise ValueError(
                    f"Unsupported plugin type: {plugin_class.PLUGIN_TYPE}"
                )

        return PluginContext(
            playqueue=self.player_context.playqueue,
            listener=self.player_context.playqueue_eventbus,
            logger=logging.getLogger(name),
            plugin_id=name,
            sdk_version=API_VERSION,
            capabilities=set(),
            config=config,
        )

    async def scan_and_setup_plugins(
        self,
        config_path: str,
        player_context: PlayerContext,
    ):
        """Scan for input modules from both entry points and legacy filesystem locations."""

        # First, scan for input modules using entry points
        self.player_context = player_context
        input_modules = {}
        devices = {}
        async for (
            plugin_name,
            prepared_plugin,
        ) in self._scan_and_setup_plugins_from_entry_points(config_path):
            plugin_type = prepared_plugin.plugin_class.PLUGIN_TYPE
            if plugin_type == PluginType.INPUT_MODULE:
                input_modules[plugin_name] = prepared_plugin
            elif plugin_type == PluginType.OUTPUT_DEVICE:
                devices[plugin_name] = prepared_plugin

        self.prepared_input_modules = {**input_modules}
        self._update_enabled_input_modules()

        self.prepared_devices = {**devices}
        self._update_enabled_devices()

modules = PreparedModuleCollection()


async def setup(config_path: str, config: KalinkaConfig) -> PlayerContext:
    """
    Setup the player components.

    Args:
        config_path: Path to the configuration file

    Returns:
        tuple: (playqueue, event_listener)
    """

    playqueue_eventbus=EventBus[PlayQueueState, PlayQueueEventType, PlayQueueEvent](  # type: ignore[type-var]
            initial_state=PlayQueueState(
                playback_state=PlaybackState(),
                track_list=[],
                playback_mode=PlaybackMode(
                    shuffle=False, repeat_single=False, repeat_all=False
                ),
            )
        )
    
    device_eventbus=EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent](  # type: ignore[type-var]
            initial_state=ExtDeviceState(power_on=False, volume=DeviceVolume()))

    # Create core components
    player_context = PlayerContext(
        playqueue_eventbus=playqueue_eventbus,
        playqueue=PlayQueueImpl(config, playqueue_eventbus),
        ext_device_eventbus=device_eventbus,
        )

    # Scan and setup plugins
    await modules.scan_and_setup_plugins(config_path, player_context)

    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    logger.info("Output devices found: %s", list(modules.prepared_devices.keys()))

    return player_context


async def shutdown_modules(modules: dict[str, PreparedPlugin], config_path: str):
    """Shutdown all modules and save their configurations."""
    for module_name, prepared_module in modules.items():
        logger.info(f"Shutting down module: {module_name}")
        if prepared_module.plugin_instance is None:
            logger.info(f"Module {module_name} was not initialized, skipping shutdown.")
            continue

        try:
            await prepared_module.plugin_instance.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down module {module_name}: {e}")

        config_file_path = os.path.join(config_path, f"{module_name}_config.cfg")
        # Ensure the directory exists
        config_dir = os.path.dirname(config_file_path)
        if config_dir:  # Only create directory if path is not empty
            os.makedirs(config_dir, exist_ok=True)

        if prepared_module.plugin_context is not None:
            with open(config_file_path, "w") as f:
                json.dump(prepared_module.plugin_context.config.model_dump(), f, indent=2)
                logger.info(f"Saved config for module {module_name} to {config_file_path}")


async def shutdown(config_path: str):
    """Shutdown all plugin modules."""
    global modules

    await shutdown_modules(modules.prepared_input_modules, config_path)
    await shutdown_modules(modules.prepared_devices, config_path)


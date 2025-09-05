from enum import Enum
import json
import logging
import os
import importlib.util
import types
from typing import Generator

from src.async_common import EventEmitter, EventListener
from queue import Queue

from src.base_config_model import ModuleConfig
from src.config_model import KalinkaConfig
from src.ext_device import ExternalOutputDevice
from src.playqueue import PlayQueue
from src.inputmodule import InputModule

logger = logging.getLogger(__name__.split(".")[-1])


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

    def setup(self, playqueue, event_emitter, event_listener):
        """Setup the module with the provided components."""
        if hasattr(self.module, "setup"):
            if self.config.enabled is False:
                logger.info(f"Skipping disabled module: {self.config.name}")
                return

            interface = self.module.setup(
                self.config, playqueue, event_emitter, event_listener
            )
            if isinstance(interface, ExternalOutputDevice) or isinstance(
                interface, InputModule
            ):
                self.interface = interface
                logger.info(f"Module {self.config.name} setup successfully.")
            else:
                raise TypeError(
                    f"Module {self.config.name} setup must return an instance of ExternalOutputDevice or InputModule."
                )
        else:
            raise AttributeError(f"Module {self.config.name} has no setup method.")

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


def scan_modules(
    addons_path: str,
) -> Generator[tuple[str, types.ModuleType], None, None]:
    """Scan addons/input_module directory and yield modules with setup function."""
    for item in os.listdir(addons_path):
        module_path = os.path.join(addons_path, item)
        if os.path.isdir(module_path) and not item.startswith("__"):
            setup_file = os.path.join(module_path, "module_setup.py")
            if os.path.exists(setup_file):
                try:
                    logger.debug(f"Loading module setup from {setup_file}")
                    spec = importlib.util.spec_from_file_location(
                        f"{item}.module_setup", setup_file
                    )
                    if spec is None or spec.loader is None:
                        logger.warning(
                            f"Could not load spec for {item}/module_setup.py"
                        )
                        continue

                    logger.debug(f"Importing module {item}")

                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    logger.debug(f"Module {item} loaded successfully")
                    if (
                        hasattr(module, "setup")
                        and hasattr(module, "Config")
                        and hasattr(module, "shutdown")
                    ):
                        yield (item, module)
                    else:
                        logger.warning(
                            f"Incorrect module: setup, shutdown and Config must be defined in {item}/module_setup.py"
                        )
                except Exception as e:
                    logger.error(
                        f"Failed to setup input module {item}: {e}", exc_info=True
                    )
                    continue
            else:
                logger.debug(f"No module_setup.py found in {item}")


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
        logger.info(
            f"Config file not found for {module_name}, creating default config."
        )

    if config_data is None:
        logger.info(f"Creating default config for {module_name} at {config_file}")
        default_config = module.Config()
        # Ensure the directory exists
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(default_config.model_dump(), f)
        return default_config

    return module.Config(**config_data)


def scan_and_setup_addons(
    config_path: str, addons_path: str, playqueue, event_emitter, event_listener
) -> Generator[tuple[str, PreparedModule], None, None]:
    if not os.path.exists(addons_path):
        logger.warning(f"Input modules directory not found: {addons_path}")

    for name, module in scan_modules(addons_path):
        logger.info(f"Found module: {name}")
        prepared_module = None
        try:
            config = read_or_create_module_config(config_path, name, module)
            prepared_module = PreparedModule(module, config)
            if config.enabled:
                prepared_module.setup(playqueue, event_emitter, event_listener)
                prepared_module.health_state = ModuleHealthState.READY
            else:
                logger.info(f"Module {name} is disabled in configuration.")
                prepared_module.health_state = ModuleHealthState.DISABLED

        except Exception as e:
            logger.error(f"Failed to setup module {name}: {e}")
            if prepared_module is not None:
                prepared_module.health_state = ModuleHealthState.ERROR
                prepared_module.error_message = str(e)

        if prepared_module is not None:
            yield name, prepared_module


def scan_and_setup_input_modules(
    config_path: str, playqueue, event_emitter, event_listener
):
    """Scan addons/input_module directory and setup enabled modules."""

    addons_path = os.path.join(
        os.path.dirname(__file__), "..", "addons", "input_module"
    )

    gen = scan_and_setup_addons(
        config_path, addons_path, playqueue, event_emitter, event_listener
    )

    modules.prepared_input_modules = {name: module for name, module in gen}
    modules.update_enabled_input_modules()


def scan_and_setup_devices(config_path: str, playqueue, event_emitter, event_listener):
    """Scan addons/device directory and setup available devices."""
    addons_path = os.path.join(os.path.dirname(__file__), "..", "addons", "device")

    gen = scan_and_setup_addons(
        config_path, addons_path, playqueue, event_emitter, event_listener
    )

    modules.prepared_devices = {name: module for name, module in gen}
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

    # Scan and setup input modules
    scan_and_setup_input_modules(config_path, playqueue, event_emitter, event_listener)

    # Scan and setup devices
    scan_and_setup_devices(config_path, playqueue, event_emitter, event_listener)

    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    logger.info("Output devices found: %s", list(modules.prepared_devices.keys()))

    return playqueue, event_listener


def shutdown_modules(modules: dict[str, PreparedModule], config_path: str):
    """Shutdown all modules and save their configurations."""
    for module_name, prepared_module in modules.items():
        logger.info(f"Shutting down module: {module_name}")
        if hasattr(prepared_module.module, "shutdown"):
            prepared_module.module.shutdown()
        config_file_path = os.path.join(config_path, f"{module_name}_config.cfg")
        # Ensure the directory exists
        os.makedirs(os.path.dirname(config_file_path), exist_ok=True)
        with open(config_file_path, "w") as f:
            json.dump(
                prepared_module.config.model_dump(exclude_unset=True), f, indent=2
            )


def shutdown(config_path: str):
    global prepared_input_modules, prepared_devices

    shutdown_modules(modules.prepared_input_modules, config_path)
    shutdown_modules(modules.prepared_devices, config_path)

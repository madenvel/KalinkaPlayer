import enum
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, AsyncGenerator, Dict, Generator, Mapping, MutableMapping

from kalinka_eventbus import EventBus
from kalinka_plugin_sdk import API_VERSION, DeviceVolume, ModuleHealthState
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
from .config_overrides import apply_overrides_with_prefix
from .playqueue import PlayQueueImpl
from kalinka_plugin_sdk.api import PlayQueueController

logger = logging.getLogger(__name__.split(".")[-1])


def _to_jsonable(value: Any) -> Any:
    """Coerce a model value into the JSON-friendly form the overrides
    file stores. Enum-typed fields read back off the model as Enum
    instances, but the on-disk override is the raw scalar (``.value``):
    without this the ``==`` comparison would always miss and the Enum
    would be written straight into the dict, crashing the later
    ``json.dumps`` in ``save_overrides``. Recurses through lists/dicts."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


@dataclass
class PlayerContext:
    playqueue: PlayQueueController
    playqueue_eventbus: EventBus[PlayQueueState, PlayQueueEventType, PlayQueueEvent]  # type: ignore[type-var]
    ext_device_eventbus: EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent]  # type: ignore[type-var]


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
    # Set by ``scan_and_setup_plugins`` when a plugin's setup mutated
    # config fields that came from the overrides file. The caller
    # (``create_app``) reads this to decide whether to re-persist the
    # overrides dict so the mutations survive a restart.
    overrides_dirty: bool = False

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

    def _build_module_config(
        self,
        plugin_name: str,
        plugin_class: type[PluginBase],
        overrides: Mapping[str, Any],
    ) -> ModuleConfig:
        """Instantiate a plugin's default config, then apply matching overrides."""
        config = plugin_class.CONFIG_MODEL()
        prefix = (
            "input_modules."
            if plugin_class.PLUGIN_TYPE == PluginType.INPUT_MODULE
            else "devices."
        )
        apply_overrides_with_prefix(config, overrides, f"{prefix}{plugin_name}.")
        return config

    def _reconcile_consumed_overrides(
        self,
        plugin_name: str,
        plugin_class: type[PluginBase],
        plugin_config: ModuleConfig,
        overrides: MutableMapping[str, Any],
    ) -> int:
        """Sync the overrides dict with any mutations the plugin's setup
        applied to its in-memory config.

        Plugins are free to mutate fields on ``context.config`` during
        ``setup()`` — for example, a "do X on next start" toggle that
        clears itself after firing. Without this reconciliation those
        mutations would only live in memory: the override loaded from
        disk would re-fire on the next boot. Compare each override key
        targeting this plugin against the current in-memory value;
        update the dict to match, dropping keys whose value reverted
        to the type default. Returns the number of override entries
        added, modified, or removed so the caller can decide whether
        to persist the file.
        """
        prefix = (
            "input_modules."
            if plugin_class.PLUGIN_TYPE == PluginType.INPUT_MODULE
            else "devices."
        ) + plugin_name + "."

        default_config = plugin_class.CONFIG_MODEL()

        def _read(model: ModuleConfig, attrs: list[str]) -> Any:
            current: Any = model
            for part in attrs:
                current = getattr(current, part)
            return current

        changed = 0
        for key in list(overrides.keys()):
            if not key.startswith(prefix):
                continue
            attrs = key[len(prefix):].split(".")
            try:
                current = _to_jsonable(_read(plugin_config, attrs))
                default = _to_jsonable(_read(default_config, attrs))
            except (AttributeError, IndexError, TypeError, ValueError):
                # The override targets a field that no longer exists or
                # is unreachable on the current model. Leave it alone —
                # apply_overrides_with_prefix already logged a warning
                # and skipped it; preserving the entry lets a future
                # schema revival pick it back up.
                continue
            stored = overrides[key]
            if current == stored:
                continue
            if current == default:
                del overrides[key]
            else:
                overrides[key] = current
            changed += 1
            logger.info(
                "Reconciled override %s: %r → %r%s",
                key,
                stored,
                current,
                " (dropped, matches default)" if current == default else "",
            )
        return changed

    async def _scan_and_setup_plugins_from_entry_points(
        self,
        overrides: MutableMapping[str, Any],
    ) -> AsyncGenerator[tuple[str, PreparedPlugin], None]:
        """Scan for installed plugins using entry points and setup those matching the specified type."""

        for plugin_name, plugin_class in self._scan_entry_points():

            logger.info(f"Found plugin: {plugin_name}")
            prepared_module = None
            error_message = None
            config = None
            plugin_context = None

            try:
                config = self._build_module_config(
                    plugin_name, plugin_class, overrides
                )
                plugin_context = self._make_plugin_context(
                    plugin_name, plugin_class, config
                )
                prepared_module = await PreparedPlugin.setup(plugin_class, plugin_context)

            except Exception as e:
                logger.error(f"Failed to setup plugin {plugin_name}: {e}", exc_info=True)
                error_message = str(e)

                # Create a PreparedPlugin with error state even if setup failed
                if config is not None and plugin_context is not None:
                    prepared_module = PreparedPlugin(
                        plugin_class=plugin_class,
                        plugin_instance=None,
                        health_state=ModuleHealthState.ERROR,
                        plugin_context=plugin_context,
                        interface=None,
                        error_message=error_message,
                    )

            # Reconcile overrides regardless of READY/ERROR state: a
            # plugin that crashed midway through setup may still have
            # consumed an override before crashing (e.g. localfiles
            # purges the DB before raising) and we don't want that
            # consumption to repeat on every restart.
            if config is not None:
                changed = self._reconcile_consumed_overrides(
                    plugin_name, plugin_class, config, overrides
                )
                if changed:
                    self.overrides_dirty = True

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
        player_context: PlayerContext,
        overrides: MutableMapping[str, Any],
    ):
        """Scan for input modules from both entry points and legacy filesystem locations."""

        # First, scan for input modules using entry points
        self.player_context = player_context
        self.overrides_dirty = False
        input_modules = {}
        devices = {}
        async for (
            plugin_name,
            prepared_plugin,
        ) in self._scan_and_setup_plugins_from_entry_points(overrides):
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


async def setup(
    config: KalinkaConfig, overrides: MutableMapping[str, Any]
) -> PlayerContext:
    """Setup the player components.

    ``overrides`` is the user-set config map (loaded from the overrides
    file); only entries whose keys begin with ``input_modules.<name>.``
    or ``devices.<name>.`` will be applied to plugin configs. The dict
    is mutated in place when a plugin's setup consumes one of its
    overrides — callers inspect ``modules.overrides_dirty`` afterwards
    to decide whether to re-persist.
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
    await modules.scan_and_setup_plugins(player_context, overrides)

    logger.info("Input modules found: %s", list(modules.prepared_input_modules.keys()))
    logger.info("Output devices found: %s", list(modules.prepared_devices.keys()))

    return player_context


async def shutdown_modules(modules: dict[str, PreparedPlugin]):
    """Shutdown all modules."""
    for module_name, prepared_module in modules.items():
        logger.info(f"Shutting down module: {module_name}")
        if prepared_module.plugin_instance is not None:
            try:
                await prepared_module.plugin_instance.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down module {module_name}: {e}")
        else:
            logger.info(f"Module {module_name} was not initialized, skipping shutdown.")


async def shutdown():
    """Shutdown all plugin modules."""
    global modules

    await shutdown_modules(modules.prepared_input_modules)
    await shutdown_modules(modules.prepared_devices)


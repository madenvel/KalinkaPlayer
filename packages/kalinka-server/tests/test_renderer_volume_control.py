"""Delegating a renderer's volume to another device module."""

from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)
from kalinka_server.player_setup import (
    ModuleHealthState,
    PreparedPlugin,
    volume_control_modules,
)
from kalinka_server.output_device_router import OutputDeviceRouter
from kalinka_server.renderer_prefs import RendererPreferences
from kalinka_server.renderer_registry import RendererRegistry


class _Device:
    def __init__(self, *functions: SupportedFunction, volume: int = 0):
        self._functions = list(functions)
        self._volume = volume

    async def get_volume(self) -> DeviceVolume:
        return DeviceVolume(
            max_volume=100, current_volume=self._volume, volume_gain=0, supported=True
        )

    async def set_volume(self, volume: int) -> None:
        return None

    async def power_on(self) -> None:
        return None

    async def power_off(self) -> None:
        return None

    async def is_power_on(self) -> bool:
        return True

    def supported_functions(self) -> list[SupportedFunction]:
        return self._functions


def _prepared(interface, health=ModuleHealthState.READY) -> PreparedPlugin:
    return PreparedPlugin(
        plugin_class=type(interface) if interface else object,  # type: ignore[arg-type]
        plugin_instance=None,
        health_state=health,
        plugin_context=None,  # type: ignore[arg-type]
        interface=interface,
    )


def test_only_live_volume_capable_modules_are_candidates():
    devices = {
        "musiccast": _prepared(
            _Device(SupportedFunction.GET_VOLUME, SupportedFunction.SET_VOLUME)
        ),
        "screen-only": _prepared(_Device(SupportedFunction.POWER_ON)),
        "broken": _prepared(
            _Device(SupportedFunction.SET_VOLUME), health=ModuleHealthState.ERROR
        ),
        "disabled": _prepared(None, health=ModuleHealthState.DISABLED),
    }
    assert volume_control_modules(devices) == ["musiccast"]


def test_the_renderer_module_is_never_a_candidate():
    devices = {
        "kalinka-renderer": _prepared(_Device(SupportedFunction.SET_VOLUME)),
    }
    assert volume_control_modules(devices) == []


def _registry_with(*renderer_ids: str) -> RendererRegistry:
    registry = RendererRegistry(prefs=RendererPreferences())
    for renderer_id in renderer_ids:
        registry.register(
            renderer_id=renderer_id,
            instance_id="inst-1",
            friendly_name=renderer_id,
            software_version="0.1.0",
            kind="native",
            platform={},
            session=object(),
        )
    return registry


async def test_router_follows_the_active_renderers_mapping():
    renderer_device, amp = _Device(SupportedFunction.SET_VOLUME), _Device(
        SupportedFunction.SET_VOLUME
    )
    devices = {
        "kalinka-renderer": _prepared(renderer_device),
        "musiccast": _prepared(amp),
    }
    registry = _registry_with("rid-a", "rid-b")
    router = OutputDeviceRouter(registry, lambda: devices)

    assert router.current() is renderer_device

    registry.set_volume_control("rid-a", "musiccast")
    assert router.current() is amp

    # rid-b has no mapping, so selecting it hands control back to the renderer.
    registry.select("rid-b")
    assert router.current() is renderer_device
    await registry.shutdown()


async def test_router_yields_nothing_when_the_owning_module_is_down():
    devices = {
        "kalinka-renderer": _prepared(_Device(SupportedFunction.SET_VOLUME)),
        "musiccast": _prepared(
            _Device(SupportedFunction.SET_VOLUME), health=ModuleHealthState.ERROR
        ),
    }
    registry = _registry_with("rid-a")
    registry.set_volume_control("rid-a", "musiccast")
    router = OutputDeviceRouter(registry, lambda: devices)

    assert router.current_name() == "musiccast"
    assert router.current() is None
    await registry.shutdown()


async def test_router_defaults_to_the_renderer_device_with_no_renderers():
    renderer_device = _Device(SupportedFunction.SET_VOLUME)
    router = OutputDeviceRouter(
        _registry_with(), lambda: {"kalinka-renderer": _prepared(renderer_device)}
    )
    assert router.current() is renderer_device


def _bus() -> EventBus:
    return EventBus[ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent](
        initial_state=ExtDeviceState(power_on=False, volume=DeviceVolume())
    )


def _volume_event(level: int) -> VolumeChangedEvent:
    return VolumeChangedEvent(
        volume=DeviceVolume(
            max_volume=100, current_volume=level, volume_gain=0, supported=True
        )
    )


async def test_only_the_owning_modules_events_reach_clients():
    """A live MusicCast reports its own volume changes whether or not it drives
    the current output; those must not be broadcast as the output's state."""
    devices = {
        "kalinka-renderer": _prepared(_Device(SupportedFunction.GET_VOLUME)),
        "musiccast": _prepared(_Device(SupportedFunction.GET_VOLUME)),
    }
    registry = _registry_with("rid-a")
    bus = _bus()
    router = OutputDeviceRouter(registry, lambda: devices, bus)
    renderer_emitter = router.emitter_for("kalinka-renderer")
    amp_emitter = router.emitter_for("musiccast")

    amp_emitter.dispatch(_volume_event(11))
    renderer_emitter.dispatch(_volume_event(22))
    assert bus.get_snapshot().volume.current_volume == 22

    registry.set_volume_control("rid-a", "musiccast")
    renderer_emitter.dispatch(_volume_event(33))
    assert bus.get_snapshot().volume.current_volume == 22
    amp_emitter.dispatch(_volume_event(44))
    assert bus.get_snapshot().volume.current_volume == 44
    await registry.shutdown()


async def test_resync_publishes_the_new_owners_state():
    devices = {
        "kalinka-renderer": _prepared(
            _Device(SupportedFunction.GET_VOLUME, volume=10)
        ),
        "musiccast": _prepared(_Device(SupportedFunction.GET_VOLUME, volume=70)),
    }
    registry = _registry_with("rid-a")
    bus = _bus()
    router = OutputDeviceRouter(registry, lambda: devices, bus)

    await router.resync()
    assert bus.get_snapshot().volume.current_volume == 10

    registry.set_volume_control("rid-a", "musiccast")
    await router.resync()
    assert bus.get_snapshot().volume.current_volume == 70
    await registry.shutdown()


async def test_resync_reports_no_volume_when_the_owner_is_down():
    devices = {
        "musiccast": _prepared(
            _Device(SupportedFunction.GET_VOLUME), health=ModuleHealthState.ERROR
        )
    }
    registry = _registry_with("rid-a")
    registry.set_volume_control("rid-a", "musiccast")
    bus = _bus()
    router = OutputDeviceRouter(registry, lambda: devices, bus)

    await router.resync()
    assert bus.get_snapshot().volume.supported is False
    await registry.shutdown()


async def test_mapping_is_per_renderer_and_survives_a_restart(tmp_path):
    path = str(tmp_path / "renderers.json")
    registry = RendererRegistry(prefs=RendererPreferences(path))
    for renderer_id in ("rid-a", "rid-b"):
        registry.register(
            renderer_id=renderer_id,
            instance_id="inst-1",
            friendly_name=renderer_id,
            software_version="0.1.0",
            kind="native",
            platform={},
            session=object(),
        )
    registry.set_volume_control("rid-a", "musiccast")

    listed = {entry["renderer_id"]: entry for entry in registry.list()}
    assert listed["rid-a"]["volume_control"] == "musiccast"
    assert listed["rid-b"]["volume_control"] is None

    revived = RendererRegistry(prefs=RendererPreferences(path))
    assert revived.volume_control("rid-a") == "musiccast"

    revived.set_volume_control("rid-a", None)
    assert RendererPreferences(path).volume_control("rid-a") is None
    await registry.shutdown()
    await revived.shutdown()

"""Volume control for the active renderer, exposed as a local output device.

The wire only carries volume inside a playback session (``set_volume`` command,
``VolumeChanged`` state), and sessions exist only while something plays. The
device bridges that gap: it caches the last volume the renderer reported, so
clients always have numbers to show, and a level set while idle is kept pending
and pushed when the next session opens — pre-setting volume before play works.

The ``volume_style`` setting decides the renderer's volume mode while this
Core plays through it. "Renderer choice" leaves whatever the renderer is
configured with; any other style rides SessionOpen as a session-scoped policy
(see ``OutputDeviceRouter.session_volume_policy``), so it is in effect before
the first playback command and undone when the session ends. It is never
written to the renderer's configuration — a renderer this Core fixed must not
stay fixed for whoever uses it next.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import ClassVar, Optional

from pydantic import Field

from kalinka_plugin_sdk.datamodel import DeviceVolume
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.plugin import OutputDevicePlugin, OutputDevicePluginContext

from .renderer_registry import RendererRegistry
from .renderer_state import StateChange
from .renderer_sessions import (
    PlaybackSession,
    SessionPool,
    SessionState,
)

logger = logging.getLogger(__name__.split(".")[-1])


class RendererVolumeStyle(str, Enum):
    renderer = "renderer"
    automatic = "automatic"
    driver = "driver"
    software = "software"
    fixed = "fixed"


# RendererVolumeStyle → renderer output.volume_mode value. "renderer" is
# absent: the renderer's own setting stays untouched.
_STYLE_TO_WIRE = {
    RendererVolumeStyle.automatic: "auto",
    RendererVolumeStyle.driver: "hardware",
    RendererVolumeStyle.software: "software",
    RendererVolumeStyle.fixed: "fixed",
}

# Dotted config path of the volume_style field on the built-in device.
VOLUME_STYLE_OPTIONS_PATH = "devices.kalinka-renderer.volume_style"

# Deliberately not full scale: a renderer whose mixer was left at maximum
# would otherwise blast on the first track this server plays through it.
DEFAULT_VOLUME = 30


def wire_volume_mode(style: RendererVolumeStyle) -> str:
    """The renderer's ``output.volume_mode`` value for a style. Empty for
    "renderer choice", which leaves the renderer's own setting alone."""
    return _STYLE_TO_WIRE.get(style, "")


def volume_style_options() -> list[dict]:
    """Labelled + described choices for the volume_style dropdown, served
    through the OptionsRegistry so the settings UI shows more than the bare
    enum values."""
    return [
        {
            "value": RendererVolumeStyle.renderer.value,
            "label": "Renderer choice",
            "description": "Whatever the renderer is set to.",
        },
        {
            "value": RendererVolumeStyle.automatic.value,
            "label": "Automatic",
            "description": "Hardware mixer if there is one, software gain "
            "otherwise.",
        },
        {
            "value": RendererVolumeStyle.driver.value,
            "label": "Driver",
            "description": "Always the hardware mixer.",
        },
        {
            "value": RendererVolumeStyle.software.value,
            "label": "Software",
            "description": "Gain applied in the player. Bit-perfect only at "
            "full volume.",
        },
        {
            "value": RendererVolumeStyle.fixed.value,
            "label": "Fixed",
            "description": "Full-level output for a mapped downstream "
            "volume device. Direct playback is refused because its safe "
            "starting level cannot be enforced.",
        },
    ]


class RendererOutputConfig(ModuleConfig):
    __module_icon__: ClassVar[str] = "speaker_outlined"
    name: str = Field(
        default="kalinka-renderer",
        title="Kalinka Renderer",
        frozen=True,
        exclude=True,
    )
    volume_style: RendererVolumeStyle = Field(
        default=RendererVolumeStyle.renderer,
        title="Volume control",
        json_schema_extra={
            "help": (
                "How volume is applied on renderers this server plays "
                "through. Set only while playing; the renderer keeps its own "
                "setting the rest of the time."
            ),
            "widget": "enum_dropdown",
            "importance": "simple",
        },
    )
    default_volume: int = Field(
        default=DEFAULT_VOLUME,
        ge=0,
        le=100,
        title="Default volume",
        json_schema_extra={
            "help": (
                "Level shown until a renderer reports its current volume, "
                "and the safe fallback for older renderers. New renderers "
                "use their own per-renderer safe-start setting."
            ),
            "importance": "simple",
        },
    )


class RendererVolumeDevice(ExternalOutputDevice):
    def __init__(
        self,
        registry: RendererRegistry,
        pool: SessionPool,
        event_emitter,
        default_volume: int = DEFAULT_VOLUME,
    ):
        self._registry = registry
        self._pool = pool
        self._event_emitter = event_emitter
        self._default_volume = default_volume
        self._session: Optional[PlaybackSession] = None
        # Last volume each renderer reported, and levels set while one was
        # idle. Both keyed by renderer: selecting another renderer must show
        # and drive that one's volume, not the previous one's.
        self._volumes: dict[str, DeviceVolume] = {}
        self._pending: dict[str, int] = {}
        self._last_sent: Optional[DeviceVolume] = None

    def _volume_for(self, renderer_id: Optional[str]) -> DeviceVolume:
        """A renderer nothing is known about reads as the default level — what
        it will be set to on first play, and a safer guess than full scale."""
        if renderer_id is not None and renderer_id in self._volumes:
            return self._volumes[renderer_id]
        return DeviceVolume(
            max_volume=100,
            current_volume=self._default_volume,
            volume_gain=0,
            supported=True,
        )

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> "RendererVolumeDevice":
        self._pool.add_open_hook(self._on_session_open)
        self._event_emitter.set_initial_state(
            ExtDeviceState(
                power_on=True, volume=self._volume_for(self._registry.active_id())
            )
        )
        # A session may already be running (module reconfigured mid-play).
        renderer_id = self._registry.active_id()
        session = self._pool.get(renderer_id) if renderer_id else None
        if session is not None and session.state is SessionState.ACTIVE:
            await self._on_session_open(session)
        return self

    async def shutdown(self) -> None:
        self._pool.remove_open_hook(self._on_session_open)

    # --------------------------------------------------- ExternalOutputDevice API
    async def get_volume(self) -> DeviceVolume:
        return self._volume_for(self._registry.active_id()).model_copy()

    async def set_volume(self, volume: int) -> None:
        renderer_id = self._registry.active_id()
        current = self._volume_for(renderer_id)
        volume = max(0, min(int(volume), current.max_volume))
        session = self._session
        if (
            session is not None
            and session.state is SessionState.ACTIVE
            and session.renderer_id == renderer_id
        ):
            try:
                # The renderer echoes VolumeChanged; that drives cache + event.
                await session.set_volume(volume)
                return
            except Exception as e:
                logger.warning("Could not set renderer volume: %s", e)
        if renderer_id is not None:
            self._pending[renderer_id] = volume
            self._volumes[renderer_id] = current.model_copy(
                update={"current_volume": volume}
            )
        self._dispatch()

    async def power_on(self) -> None:
        return None

    async def is_power_on(self) -> bool:
        return True

    async def power_off(self) -> None:
        return None

    def supported_functions(self) -> list[SupportedFunction]:
        if self._volume_for(self._registry.active_id()).supported:
            return [SupportedFunction.GET_VOLUME, SupportedFunction.SET_VOLUME]
        return []

    # ------------------------------------------------------------------ internals
    async def _on_session_open(self, session: PlaybackSession) -> None:
        self._session = session
        session.on_state(self._on_session_state)
        session.on_closed(self._on_session_closed)
        # The volume mode rides SessionOpen itself (see
        # OutputDeviceRouter.session_volume_policy), so it is already in
        # effect here and nothing was written to the renderer's config.
        await self._apply_pending(session)

    async def _apply_pending(self, session: PlaybackSession) -> None:
        pending = self._pending.pop(session.renderer_id, None)
        if pending is None:
            return
        try:
            await session.set_volume(pending)
        except Exception as e:
            logger.warning("Could not apply pending volume: %s", e)

    async def _on_session_state(
        self, session: PlaybackSession, change: StateChange, snapshot: dict
    ) -> None:
        if session is not self._session:
            return
        if change not in (StateChange.VOLUME, StateChange.SNAPSHOT):
            return
        volume = snapshot.get("volume")
        if volume is None:
            return
        self._volumes[session.renderer_id] = DeviceVolume(
            max_volume=volume["max"] or 100,
            current_volume=volume["current"],
            volume_gain=0,
            supported=volume["supported"],
        )
        self._dispatch()
        # A resumed session never re-runs the open hook; settle a level set
        # while the renderer was away.
        if (
            session.renderer_id in self._pending
            and session.state is SessionState.ACTIVE
        ):
            await self._apply_pending(session)

    def _on_session_closed(self, session: PlaybackSession, reason) -> None:
        if session is self._session:
            self._session = None

    def _dispatch(self) -> None:
        """Put the active renderer's cached volume on the device bus, skipping
        exact repeats (a snapshot restating the level must not re-notify every
        client)."""
        volume = self._volume_for(self._registry.active_id())
        if volume == self._last_sent:
            return
        self._last_sent = volume
        self._event_emitter.dispatch(
            VolumeChangedEvent.model_construct(
                event_type=ExtDeviceEventType.VolumeChanged,
                volume=volume,
            )
        )


class RendererOutputPlugin(OutputDevicePlugin):
    """Built-in output device for the connected renderer.

    Not loaded via entry points — ``player_setup`` constructs it and calls
    :meth:`bind` to inject the renderer services (which the plugin context
    can't carry), then runs the normal ``setup()``/``shutdown()`` lifecycle so
    it participates in the device registry and settings page.
    """

    REQUIRES_SDK = ">=1.0,<2"
    PLUGIN_ID = "kalinka-renderer"
    CONFIG_MODEL = RendererOutputConfig

    def __init__(self):
        self._device: Optional[RendererVolumeDevice] = None
        self._registry: Optional[RendererRegistry] = None
        self._pool: Optional[SessionPool] = None

    def bind(self, registry: RendererRegistry, pool: SessionPool) -> None:
        self._registry = registry
        self._pool = pool

    async def setup(self, context: OutputDevicePluginContext) -> None:
        if self._registry is None or self._pool is None:
            raise RuntimeError(
                "RendererOutputPlugin.bind() must be called before setup()"
            )
        self._device = RendererVolumeDevice(
            self._registry,
            self._pool,
            context.emitter,
            getattr(context.config, "default_volume", DEFAULT_VOLUME),
        )
        await self._device.start()

    async def shutdown(self) -> None:
        if self._device is not None:
            await self._device.shutdown()
        self._device = None

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

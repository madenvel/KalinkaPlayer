"""Volume control for the active renderer, exposed as a local output device.

The wire only carries volume inside a playback session (``set_volume`` command,
``VolumeChanged`` state), and sessions exist only while something plays. The
device bridges that gap: it caches the last volume the renderer reported, so
clients always have numbers to show, and a level set while idle is kept pending
and pushed when the next session opens — pre-setting volume before play works.

The ``volume_style`` setting decides who owns the renderer's volume mode
(``output.volume_mode`` in the renderer's own config plane). "Renderer choice"
leaves it alone; any other style is pushed to the renderer through the
config plane when a session opens. The push runs inside ``SessionPool.open()``
(see ``add_open_hook``), so it always lands before the first playback command.
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

from .renderer_config import RendererConfigService
from .renderer_registry import RendererRegistry
from .renderer_sessions import (
    PlaybackSession,
    SessionPool,
    SessionState,
)

logger = logging.getLogger(__name__.split(".")[-1])

# The renderer's volume-mode config field (see NativePlayer::fillConfig).
_VOLUME_MODE_PATH = "output.volume_mode"


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


def volume_style_options() -> list[dict]:
    """Labelled + described choices for the volume_style dropdown, served
    through the OptionsRegistry so the settings UI shows more than the bare
    enum values."""
    return [
        {
            "value": RendererVolumeStyle.renderer.value,
            "label": "Renderer choice",
            "description": "Leave volume control as configured on the "
            "renderer itself.",
        },
        {
            "value": RendererVolumeStyle.automatic.value,
            "label": "Automatic",
            "description": "Use the renderer's device mixer when it has one "
            "(bit-perfect); otherwise apply software gain.",
        },
        {
            "value": RendererVolumeStyle.driver.value,
            "label": "Driver",
            "description": "Always use the renderer's device mixer — "
            "bit-perfect in Kalinka, though the driver may apply it in "
            "software.",
        },
        {
            "value": RendererVolumeStyle.software.value,
            "label": "Software",
            "description": "Apply gain in the renderer's player. Works on any "
            "device, but only bit-perfect at full volume.",
        },
        {
            "value": RendererVolumeStyle.fixed.value,
            "label": "Fixed",
            "description": "No volume control; bit-perfect output at full "
            "scale (control volume downstream).",
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
                "How the volume slider drives the renderer. \"Renderer "
                "choice\" keeps whatever is configured on the renderer; any "
                "other choice overrides it whenever playback starts."
            ),
            "widget": "enum_dropdown",
            "importance": "simple",
        },
    )


class RendererVolumeDevice(ExternalOutputDevice):
    def __init__(
        self,
        registry: RendererRegistry,
        pool: SessionPool,
        configs: RendererConfigService,
        event_emitter,
        style: RendererVolumeStyle,
    ):
        self._registry = registry
        self._pool = pool
        self._configs = configs
        self._event_emitter = event_emitter
        self._style = style
        self._session: Optional[PlaybackSession] = None
        # Last volume the renderer reported; shown while no session exists.
        self._volume = DeviceVolume(
            max_volume=100, current_volume=100, volume_gain=0, supported=True
        )
        # A level set while idle, applied when the next session opens.
        self._pending: Optional[int] = None
        self._last_sent: Optional[DeviceVolume] = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> "RendererVolumeDevice":
        self._pool.add_open_hook(self._on_session_open)
        self._event_emitter.set_initial_state(
            ExtDeviceState(power_on=True, volume=self._volume)
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
        return self._volume.model_copy()

    async def set_volume(self, volume: int) -> None:
        volume = max(0, min(int(volume), self._volume.max_volume))
        session = self._session
        if session is not None and session.state is SessionState.ACTIVE:
            try:
                # The renderer echoes VolumeChanged; that drives cache + event.
                await session.set_volume(volume)
                return
            except Exception as e:
                logger.warning("Could not set renderer volume: %s", e)
        self._pending = volume
        self._volume = self._volume.model_copy(update={"current_volume": volume})
        self._dispatch()

    async def power_on(self) -> None:
        return None

    async def is_power_on(self) -> bool:
        return True

    async def power_off(self) -> None:
        return None

    def supported_functions(self) -> list[SupportedFunction]:
        if self._volume.supported:
            return [SupportedFunction.GET_VOLUME, SupportedFunction.SET_VOLUME]
        return []

    # ------------------------------------------------------------------ internals
    async def _on_session_open(self, session: PlaybackSession) -> None:
        self._session = session
        session.on_state(self._on_session_state)
        session.on_closed(self._on_session_closed)
        if self._style is not RendererVolumeStyle.renderer:
            try:
                await self._configs.update(
                    session.renderer_id,
                    {_VOLUME_MODE_PATH: _STYLE_TO_WIRE[self._style]},
                )
            except Exception as e:
                logger.warning(
                    "Could not push volume mode '%s' to renderer %s: %s",
                    self._style.value,
                    session.renderer_id,
                    e,
                )
        await self._apply_pending(session)

    async def _apply_pending(self, session: PlaybackSession) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        try:
            await session.set_volume(pending)
        except Exception as e:
            logger.warning("Could not apply pending volume: %s", e)

    async def _on_session_state(
        self, session: PlaybackSession, payload: str, snapshot: dict
    ) -> None:
        if session is not self._session:
            return
        if payload not in ("volume_changed", "state_snapshot"):
            return
        volume = snapshot.get("volume")
        if volume is None:
            return
        self._volume = DeviceVolume(
            max_volume=volume["max"] or 100,
            current_volume=volume["current"],
            volume_gain=0,
            supported=volume["supported"],
        )
        self._dispatch()
        # A resumed session never re-runs the open hook; settle a level set
        # while the renderer was away.
        if self._pending is not None and session.state is SessionState.ACTIVE:
            await self._apply_pending(session)

    def _on_session_closed(self, session: PlaybackSession, reason) -> None:
        if session is self._session:
            self._session = None

    def _dispatch(self) -> None:
        """Put the cached volume on the device bus, skipping exact repeats
        (a snapshot restating the level must not re-notify every client)."""
        volume = self._volume
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
        self._configs: Optional[RendererConfigService] = None

    def bind(
        self,
        registry: RendererRegistry,
        pool: SessionPool,
        configs: RendererConfigService,
    ) -> None:
        self._registry = registry
        self._pool = pool
        self._configs = configs

    async def setup(self, context: OutputDevicePluginContext) -> None:
        if self._registry is None or self._pool is None or self._configs is None:
            raise RuntimeError(
                "RendererOutputPlugin.bind() must be called before setup()"
            )
        style = getattr(
            context.config, "volume_style", RendererVolumeStyle.renderer
        )
        self._device = RendererVolumeDevice(
            self._registry,
            self._pool,
            self._configs,
            context.emitter,
            style,
        )
        await self._device.start()

    async def shutdown(self) -> None:
        if self._device is not None:
            await self._device.shutdown()
        self._device = None

    def get_interface(self) -> Optional[ExternalOutputDevice]:
        return self._device

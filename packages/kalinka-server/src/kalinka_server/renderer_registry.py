"""Registry of known renderers, keyed by stable renderer_id (never by name).

Same instance_id on return = reconnect; new instance_id = restart. A dropped
link goes OFFLINE and is reaped after a timeout; a Goodbye removes at once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

from kalinka_plugin_sdk.events import RendererDescriptor

from .renderer_link import RendererLink
from .renderer_prefs import RendererPreferences

logger = logging.getLogger(__name__.split(".")[-1])

DEFAULT_OFFLINE_TIMEOUT_S = 60.0


class RendererStatus(str, Enum):
    CONNECTED = "connected"
    OFFLINE = "offline"


class RegistrationKind(str, Enum):
    NEW = "new"
    RECONNECT = "reconnect"  # same renderer_id + same instance_id
    RESTART = "restart"      # same renderer_id, new instance_id


class RendererUnavailable(Exception):
    """The renderer is not connected, so nothing can be asked of it."""


@dataclass
class RendererRecord:
    renderer_id: str
    instance_id: str
    friendly_name: str
    software_version: str
    kind: str
    platform: dict[str, str]
    connected_at: float
    last_seen: float
    # The server (host, port) the renderer dialed to register — an address it
    # provably reaches, which no server-side guess can promise.
    server_addr: Optional[tuple[str, int]] = None
    # The renderer's connection while it has one; compared by identity.
    session: Optional[RendererLink] = field(default=None, repr=False)

    @property
    def status(self) -> RendererStatus:
        """Holding a session is what being connected means; not stored twice."""
        return (
            RendererStatus.CONNECTED
            if self.session is not None
            else RendererStatus.OFFLINE
        )

    def descriptor(self) -> RendererDescriptor:
        return RendererDescriptor(
            renderer_id=self.renderer_id,
            instance_id=self.instance_id,
            friendly_name=self.friendly_name,
            software_version=self.software_version,
            kind=self.kind,
            status=self.status.value,
            platform=self.platform,
            connected_at=self.connected_at,
            last_seen=self.last_seen,
        )


class RendererRegistry:
    def __init__(
        self,
        offline_timeout_s: float = DEFAULT_OFFLINE_TIMEOUT_S,
        replace_session: Optional[Callable[[RendererLink], Awaitable[None]]] = None,
        prefs: Optional[RendererPreferences] = None,
    ):
        self.offline_timeout_s = offline_timeout_s
        self._replace_session = replace_session
        self._on_removed: Optional[Callable[[str, bool], None]] = None
        self._on_renderers_changed: Optional[
            Callable[[list[RendererDescriptor]], None]
        ] = None
        self._on_current_changed: Optional[
            Callable[[Optional[str], Optional[str]], None]
        ] = None
        # None until the first report, so subscribing always states the pair.
        self._last_current: Optional[tuple[Optional[str], Optional[str]]] = None
        self._renderers: dict[str, RendererRecord] = {}
        self._reap_tasks: dict[str, asyncio.Task] = {}
        self._replace_tasks: set[asyncio.Task] = set()
        # Held for the selection only: which renderer plays is registry state
        # that has to survive a restart. The store's other entries belong to
        # whoever owns that concern (volume delegation -> OutputDeviceRouter).
        self._prefs = prefs if prefs is not None else RendererPreferences()

    def register(
        self,
        *,
        renderer_id: str,
        instance_id: str,
        friendly_name: str,
        software_version: str,
        kind: str,
        platform: dict[str, str],
        session: RendererLink,
        server_addr: Optional[tuple[str, int]] = None,
    ) -> RegistrationKind:
        self._cancel_reap(renderer_id)
        now = time.time()
        existing = self._renderers.get(renderer_id)

        registration = RegistrationKind.NEW
        connected_at = now
        if existing is not None:
            if existing.session is not None and existing.session is not session:
                # Newest connection wins; the old one is retired with
                # REASON_REPLACED and its late disconnect is ignored.
                logger.info(
                    "Renderer %s reconnected while a session was live; "
                    "replacing the old session",
                    renderer_id,
                )
                if self._replace_session is not None:
                    self._spawn_replace(existing.session)
            if existing.instance_id == instance_id:
                registration = RegistrationKind.RECONNECT
                connected_at = existing.connected_at
            else:
                registration = RegistrationKind.RESTART

        self._renderers[renderer_id] = RendererRecord(
            renderer_id=renderer_id,
            instance_id=instance_id,
            friendly_name=friendly_name,
            software_version=software_version,
            kind=kind,
            platform=platform,
            connected_at=connected_at,
            last_seen=now,
            server_addr=server_addr,
            session=session,
        )
        logger.info(
            "Renderer %s: '%s' (%s, id=%s)",
            registration.value,
            friendly_name,
            kind,
            renderer_id,
        )
        self._publish_topology()
        return registration

    def disconnect(self, renderer_id: str, session: RendererLink, clean: bool) -> None:
        """`clean` = renderer sent Goodbye: remove now instead of OFFLINE-wait."""
        record = self._renderers.get(renderer_id)
        if record is None or record.session is not session:
            return  # stale disconnect of a replaced session
        record.session = None
        record.last_seen = time.time()
        if clean:
            del self._renderers[renderer_id]
            logger.info(
                "Renderer '%s' (id=%s) left cleanly; removed",
                record.friendly_name,
                renderer_id,
            )
            self._notify_removed(renderer_id, clean=True)
            self._publish_topology()
            return
        logger.info(
            "Renderer '%s' (id=%s) offline; keeping for %.0fs",
            record.friendly_name,
            renderer_id,
            self.offline_timeout_s,
        )
        self._schedule_reap(renderer_id)
        self._publish_topology()

    def set_on_removed(self, callback: Callable[[str, bool], None]) -> None:
        """Set the removal hook after construction (the session pool needs the
        registry to exist first)."""
        self._on_removed = callback

    def _notify_removed(self, renderer_id: str, clean: bool) -> None:
        if self._on_removed is not None:
            self._on_removed(renderer_id, clean)

    def set_on_changed(
        self,
        *,
        renderers: Callable[[list[RendererDescriptor]], None],
        current: Callable[[Optional[str], Optional[str]], None],
    ) -> None:
        """Subscribe to topology changes, and report the picture at once.

        ``renderers`` takes the full descriptor snapshot whenever membership or
        status moves; ``current`` takes (active, selected) when that pair moves
        — a renderer going offline shifts playback with no selection touched.
        Both fire here as well, so a subscriber starts from the truth instead of
        from its first change: a selection restored from prefs has to reach
        clients before any renderer connects.
        """
        self._on_renderers_changed = renderers
        self._on_current_changed = current
        self._publish_topology()

    def _descriptors(self) -> list[RendererDescriptor]:
        return [
            record.descriptor()
            for record in sorted(
                self._renderers.values(), key=lambda r: r.friendly_name
            )
        ]

    def _publish_topology(self) -> None:
        """Membership or status moved, which can move the current pair too."""
        self._publish_renderers()
        self._publish_current()

    def _publish_renderers(self) -> None:
        if self._on_renderers_changed is not None:
            self._on_renderers_changed(self._descriptors())

    def _publish_current(self) -> None:
        # Nothing is recorded without a subscriber, so the first report after
        # one arrives is never mistaken for a repeat.
        if self._on_current_changed is None:
            return
        current = (self.active_id(), self.selected_id)
        if current == self._last_current:
            return
        self._last_current = current
        self._on_current_changed(*current)

    def get(self, renderer_id: str) -> Optional[RendererRecord]:
        return self._renderers.get(renderer_id)

    def live_session(self, renderer_id: str) -> Optional[RendererLink]:
        """The renderer's session handle while it is connected, else None."""
        record = self._renderers.get(renderer_id)
        return record.session if record is not None else None

    def require_session(self, renderer_id: str) -> RendererLink:
        """Same, for callers that have nothing to say to a renderer that is
        not there."""
        session = self.live_session(renderer_id)
        if session is None:
            raise RendererUnavailable(f"renderer {renderer_id} is not connected")
        return session

    def _first_connected_id(self) -> Optional[str]:
        """Earliest-registered renderer that is connected right now."""
        for renderer_id, record in self._renderers.items():
            if record.session is not None:
                return renderer_id
        return None

    def select(self, renderer_id: Optional[str]) -> None:
        """Pin playback to a renderer; None returns to automatic."""
        self._prefs.set_selected(renderer_id)
        logger.info(
            "Renderer selection: %s", renderer_id if renderer_id else "automatic"
        )
        self._publish_current()

    @property
    def selected_id(self) -> Optional[str]:
        return self._prefs.selected_renderer_id

    def active_id(self) -> Optional[str]:
        """The renderer playback opens sessions on: the selected one while it
        is connected, otherwise the first connected. A selected renderer that
        is offline is not forgotten — it wins again when it returns."""
        return self.resolve_active(self._prefs.selected_renderer_id)

    def resolve_active(self, selected_id: Optional[str]) -> Optional[str]:
        """What :meth:`active_id` would return for a given selection. Lets a
        caller see where playback is headed before committing the choice."""
        if selected_id and self.live_session(selected_id) is not None:
            return selected_id
        return self._first_connected_id()

    def list(self) -> list[dict]:
        active = self.active_id()
        selected = self.selected_id
        return [
            descriptor.model_dump()
            | {
                "active": descriptor.renderer_id == active,
                "selected": descriptor.renderer_id == selected,
            }
            for descriptor in self._descriptors()
        ]

    def _spawn_replace(self, session: RendererLink) -> None:
        assert self._replace_session is not None
        # Held onto: the loop keeps only a weak reference, and a task collected
        # mid-flight would leave the replaced session running.
        task = asyncio.ensure_future(self._replace_session(session))
        self._replace_tasks.add(task)
        task.add_done_callback(self._replace_done)

    def _replace_done(self, task: asyncio.Task) -> None:
        self._replace_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Retiring a replaced renderer session failed: %s", exc)

    def _schedule_reap(self, renderer_id: str) -> None:
        self._cancel_reap(renderer_id)
        self._reap_tasks[renderer_id] = asyncio.create_task(
            self._reap(renderer_id)
        )

    def _cancel_reap(self, renderer_id: str) -> None:
        task = self._reap_tasks.pop(renderer_id, None)
        if task is not None:
            task.cancel()

    async def _reap(self, renderer_id: str) -> None:
        await asyncio.sleep(self.offline_timeout_s)
        self._reap_tasks.pop(renderer_id, None)
        record = self._renderers.get(renderer_id)
        if record is not None and record.session is None:
            del self._renderers[renderer_id]
            logger.info(
                "Renderer '%s' (id=%s) did not return within %.0fs; removed",
                record.friendly_name,
                renderer_id,
                self.offline_timeout_s,
            )
            self._notify_removed(renderer_id, clean=False)
            self._publish_topology()

    async def shutdown(self) -> None:
        pending = [*self._reap_tasks.values(), *self._replace_tasks]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._reap_tasks.clear()
        self._replace_tasks.clear()

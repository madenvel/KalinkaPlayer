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
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__.split(".")[-1])

DEFAULT_OFFLINE_TIMEOUT_S = 60.0


class RendererStatus(str, Enum):
    CONNECTED = "connected"
    OFFLINE = "offline"


class RegistrationKind(str, Enum):
    NEW = "new"
    RECONNECT = "reconnect"  # same renderer_id + same instance_id
    RESTART = "restart"      # same renderer_id, new instance_id


@dataclass
class RendererRecord:
    renderer_id: str
    instance_id: str
    friendly_name: str
    software_version: str
    kind: str
    platform: dict[str, str]
    status: RendererStatus
    connected_at: float
    last_seen: float
    # Opaque WS-handler handle; compared by identity, `replace` is called on it.
    session: Optional[Any] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "renderer_id": self.renderer_id,
            "instance_id": self.instance_id,
            "friendly_name": self.friendly_name,
            "software_version": self.software_version,
            "kind": self.kind,
            "status": self.status.value,
            "platform": self.platform,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
        }


class RendererRegistry:
    def __init__(
        self,
        offline_timeout_s: float = DEFAULT_OFFLINE_TIMEOUT_S,
        replace_session: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        self.offline_timeout_s = offline_timeout_s
        self._replace_session = replace_session
        self._renderers: dict[str, RendererRecord] = {}
        self._reap_tasks: dict[str, asyncio.Task] = {}

    def register(
        self,
        *,
        renderer_id: str,
        instance_id: str,
        friendly_name: str,
        software_version: str,
        kind: str,
        platform: dict[str, str],
        session: Any,
    ) -> RegistrationKind:
        self._cancel_reap(renderer_id)
        now = time.time()
        existing = self._renderers.get(renderer_id)

        registration = RegistrationKind.NEW
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
                    asyncio.create_task(self._replace_session(existing.session))
            registration = (
                RegistrationKind.RECONNECT
                if existing.instance_id == instance_id
                else RegistrationKind.RESTART
            )

        self._renderers[renderer_id] = RendererRecord(
            renderer_id=renderer_id,
            instance_id=instance_id,
            friendly_name=friendly_name,
            software_version=software_version,
            kind=kind,
            platform=platform,
            status=RendererStatus.CONNECTED,
            connected_at=(
                existing.connected_at
                if existing is not None
                and registration == RegistrationKind.RECONNECT
                else now
            ),
            last_seen=now,
            session=session,
        )
        logger.info(
            "Renderer %s: '%s' (%s, id=%s)",
            registration.value,
            friendly_name,
            kind,
            renderer_id,
        )
        return registration

    def disconnect(self, renderer_id: str, session: Any, clean: bool) -> None:
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
            return
        record.status = RendererStatus.OFFLINE
        logger.info(
            "Renderer '%s' (id=%s) offline; keeping for %.0fs",
            record.friendly_name,
            renderer_id,
            self.offline_timeout_s,
        )
        self._schedule_reap(renderer_id)

    def get(self, renderer_id: str) -> Optional[RendererRecord]:
        return self._renderers.get(renderer_id)

    def list(self) -> list[dict]:
        return [
            record.to_dict()
            for record in sorted(
                self._renderers.values(), key=lambda r: r.friendly_name
            )
        ]

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
        if record is not None and record.status == RendererStatus.OFFLINE:
            del self._renderers[renderer_id]
            logger.info(
                "Renderer '%s' (id=%s) did not return within %.0fs; removed",
                record.friendly_name,
                renderer_id,
                self.offline_timeout_s,
            )

    async def shutdown(self) -> None:
        for task in self._reap_tasks.values():
            task.cancel()
        if self._reap_tasks:
            await asyncio.gather(
                *self._reap_tasks.values(), return_exceptions=True
            )
        self._reap_tasks.clear()

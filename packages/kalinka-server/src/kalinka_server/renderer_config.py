"""Renderer settings: read and write, over any connection, without a session.

Settings belong to the renderer, not to whoever is playing through it, so this
plane never touches the session pool — a Core shows a renderer's settings page
without claiming its audio graph, and several Cores may edit the same renderer.
Last write wins and nobody is notified: writes name individual paths, so a page
that went stale cannot clobber a field it did not touch, and the reply carries
the values that ended up in effect.

The renderer owns the values, and answers with schema and values together, so
one round trip is a whole settings page with its device list as fresh as the
request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from . import renderer_state
from .renderer_link import RendererLink
from .renderer_proto import renderer_pb2 as pb
from .renderer_registry import RendererRegistry, RendererUnavailable
from .renderer_sessions import DEFAULT_TIMEOUT_S

logger = logging.getLogger(__name__.split(".")[-1])


def _importance(field) -> str:
    """Which page the field belongs on, as the renderer sees it.

    A renderer from before the tier says nothing, and what it declares is what
    a settings page already showed: silence means the page proper, not the
    expert list its fields have never been in.
    """
    if field.importance == pb.CONFIG_IMPORTANCE_UNSPECIFIED:
        return "simple"
    return renderer_state.enum_name(
        pb.ConfigImportance, field.importance, "CONFIG_IMPORTANCE_"
    )


def _field_to_dict(field) -> dict:
    return {
        "path": field.path,
        "title": field.title,
        "description": field.description,
        "type": renderer_state.enum_name(
            pb.ConfigFieldType, field.type, "CONFIG_FIELD_TYPE_"
        ),
        "value": field.value,
        "default": field.default_value,
        "options": [
            {
                "value": option.value,
                "label": option.label,
                "description": option.description,
            }
            for option in field.options
        ],
        "apply": renderer_state.enum_name(pb.ApplyCost, field.apply, "APPLY_COST_"),
        "read_only": field.read_only,
        "importance": _importance(field),
    }


def snapshot_to_dict(snapshot) -> dict:
    return {
        "config_version": snapshot.config_version,
        "sections": [
            {
                "path": section.path,
                "title": section.title,
                "description": section.description,
                "fields": [_field_to_dict(f) for f in section.fields],
            }
            for section in snapshot.sections
        ],
    }


def result_to_dict(result) -> dict:
    return {
        "config_version": result.config_version,
        "effect": renderer_state.enum_name(pb.ApplyCost, result.effect, "APPLY_COST_"),
        "outcomes": [
            {
                "path": outcome.path,
                "applied": outcome.applied,
                "value": outcome.value,
                "error": outcome.error,
            }
            for outcome in result.outcomes
        ],
    }


class RendererConfigService:
    """One in-flight request per call, matched by the envelope's message_id."""

    def __init__(
        self, registry: RendererRegistry, timeout_s: float = DEFAULT_TIMEOUT_S
    ):
        self._registry = registry
        self._timeout_s = timeout_s
        self._pending: dict[tuple[str, int], asyncio.Future] = {}

    async def get(self, renderer_id: str) -> dict:
        reply = await self._request(renderer_id, "config")
        return snapshot_to_dict(reply)

    async def update(self, renderer_id: str, changes: dict[str, str]) -> dict:
        reply = await self._request(renderer_id, "config update", changes=changes)
        applied = [o for o in reply.outcomes if o.applied]
        if applied:
            logger.info(
                "Renderer %s applied %s",
                renderer_id,
                ", ".join(f"{o.path}={o.value!r}" for o in applied),
            )
        return result_to_dict(reply)

    async def _request(
        self, renderer_id: str, what: str, changes: Optional[dict[str, str]] = None
    ) -> Any:
        ws = self._connection(renderer_id)
        # Reserved before the write: the answer must never be able to arrive
        # before there is something waiting for it.
        message_id = ws.next_message_id()
        key = (renderer_id, message_id)
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            if changes is None:
                await ws.send_config_request(message_id)
            else:
                await ws.send_config_update(message_id, changes)
            return await asyncio.wait_for(future, self._timeout_s)
        except asyncio.TimeoutError:
            logger.warning("Renderer %s did not answer for %s", renderer_id, what)
            raise
        finally:
            self._pending.pop(key, None)

    def _connection(self, renderer_id: str) -> RendererLink:
        return self._registry.require_session(renderer_id)

    def handle_reply(self, renderer_id: str, in_reply_to: int, message: Any) -> None:
        future = self._pending.get((renderer_id, in_reply_to))
        if future is None or future.done():
            logger.debug(
                "Ignoring config reply to message %d from %s, which nobody awaits",
                in_reply_to,
                renderer_id,
            )
            return
        future.set_result(message)

    def handle_disconnect(self, renderer_id: str) -> None:
        """Nothing is coming back on a socket that is gone."""
        for key, future in list(self._pending.items()):
            if key[0] == renderer_id and not future.done():
                future.set_exception(
                    RendererUnavailable(
                        f"renderer {renderer_id} disconnected before answering"
                    )
                )

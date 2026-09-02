"""Upgrading the renderers registered with this Core.

A renderer usually runs on another machine, so the connection it holds here is
often the only way to reach it — most of all when a protocol bump has left it
unable to play, which is exactly when it needs replacing. The Upgrade message
is carried by every protocol version for that reason, and this service sends it
over the raw link rather than the drivable one.

Two rules keep an upgrade from being disruptive: a renderer running a playback
session is never asked (it would cut the track off mid-play), and a renderer
that cannot install a release of itself is never offered one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from .renderer_link import RendererLink
from .renderer_registry import RendererRegistry, RendererUnavailable
from .update_check import is_newer

logger = logging.getLogger(__name__.split(".")[-1])

DEFAULT_TIMEOUT_S = 10.0


class UpgradeRefused(Exception):
    """The renderer would not take the upgrade on, and said why."""


@dataclass(frozen=True)
class UpgradeCandidate:
    """A registered renderer an installer run here would bring forward."""

    renderer_id: str
    friendly_name: str
    installed_version: str
    busy: bool


class RendererUpgradeService:
    """One in-flight request per renderer, matched by the envelope's id."""

    def __init__(
        self,
        registry: RendererRegistry,
        is_busy: Callable[[str], bool],
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        """``is_busy`` says whether playback is running on a renderer — the
        one question this service asks about sessions, so it depends on that
        rather than on the pool that answers it."""
        self._registry = registry
        self._is_busy = is_busy
        self._timeout_s = timeout_s
        self._pending: dict[tuple[str, int], asyncio.Future] = {}

    def candidates(self, latest_version: Optional[str]) -> list[UpgradeCandidate]:
        """Connected native renderers behind ``latest_version`` that could take
        an upgrade. Empty while no release is known — silence is not a verdict.
        """
        return self._behind(latest_version, can_upgrade=True)

    def stranded(self, latest_version: Optional[str]) -> list[UpgradeCandidate]:
        """The ones behind that cannot install a release of themselves.

        Nothing this Core does brings them forward, so they are reported
        rather than waited for — a flatpak or from-source renderer would
        otherwise hold every other upgrade back for ever.
        """
        return self._behind(latest_version, can_upgrade=False)

    def _behind(
        self, latest_version: Optional[str], *, can_upgrade: bool
    ) -> list[UpgradeCandidate]:
        if not latest_version:
            return []
        found = []
        for record in self._registry.records():
            if record.kind != "native" or record.session is None:
                continue
            if record.upgrade_supported is not can_upgrade:
                continue
            if not version_is_newer(latest_version, record.software_version):
                continue
            found.append(
                UpgradeCandidate(
                    renderer_id=record.renderer_id,
                    friendly_name=record.friendly_name,
                    installed_version=record.software_version,
                    busy=self._is_busy(record.renderer_id),
                )
            )
        return found

    async def bring_forward(self, latest_version: Optional[str]) -> bool:
        """Upgrade every renderer ``latest_version`` would bring forward.

        Returns whether nothing needed doing. False means one was asked and
        has not come back yet, so a caller with its own upgrade to make — the
        Core, whose next release may move the protocol — should look again
        later rather than moving past them. One that is playing is left for
        that later look; one that cannot install a release of itself is said
        out loud and not waited for, because no later look would change it.
        """
        for stranded in self.stranded(latest_version):
            logger.warning(
                "Renderer '%s' is on %s and cannot upgrade itself",
                stranded.friendly_name,
                stranded.installed_version,
            )
        behind = self.candidates(latest_version)
        if not behind or latest_version is None:
            return True
        for candidate in behind:
            if candidate.busy:
                logger.info(
                    "Renderer '%s' is playing; leaving its upgrade for later",
                    candidate.friendly_name,
                )
                continue
            try:
                await self.upgrade(candidate.renderer_id, latest_version)
            except Exception as e:  # noqa: BLE001 — one bad renderer, not all
                logger.warning(
                    "Renderer '%s' did not take the upgrade: %s",
                    candidate.friendly_name,
                    e,
                )
        return False

    async def upgrade(self, renderer_id: str, target_version: str) -> str:
        """Ask one renderer to install ``target_version``; returns its detail.

        Raises :class:`RendererUnavailable` when it is not connected and
        :class:`UpgradeRefused` when it declines — a session running on it,
        or an install that cannot replace itself.
        """
        record = self._registry.get(renderer_id)
        if record is None or record.session is None:
            # Named, not numbered: these reach a person, who knows the
            # renderer by what it calls itself and never by its id.
            name = record.friendly_name if record else renderer_id
            raise RendererUnavailable(f"{name} is not connected")
        name = record.friendly_name or renderer_id
        if not record.upgrade_supported:
            raise UpgradeRefused(f"{name} cannot install a release of itself")
        if self._is_busy(renderer_id):
            raise UpgradeRefused(f"{name} is playing right now")
        result = await self._request(record.session, renderer_id, target_version)
        if not result.accepted:
            raise UpgradeRefused(
                f"{name} refused: {result.detail}"
                if result.detail
                else f"{name} refused the upgrade"
            )
        logger.info(
            "Renderer '%s' (id=%s) is upgrading to %s",
            record.friendly_name,
            renderer_id,
            target_version or "the latest release",
        )
        return result.detail

    async def _request(
        self, link: RendererLink, renderer_id: str, target_version: str
    ):
        # Reserved before the write: the answer must never be able to arrive
        # before there is something waiting for it.
        message_id = link.next_message_id()
        key = (renderer_id, message_id)
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            await link.send_upgrade(message_id, target_version)
            return await asyncio.wait_for(future, self._timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "Renderer %s did not answer the upgrade request", renderer_id
            )
            raise
        finally:
            self._pending.pop(key, None)

    def handle_reply(self, renderer_id: str, in_reply_to: int, message) -> None:
        future = self._pending.get((renderer_id, in_reply_to))
        if future is None or future.done():
            logger.debug(
                "Ignoring upgrade reply to message %d from %s, which nobody "
                "awaits",
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


def version_is_newer(candidate: str, installed: str) -> bool:
    """Whether ``candidate`` is a later release than ``installed``.

    Ordered by the release each version leads to, not by packaging rules: the
    deb ordering :func:`update_check.deb_is_newer` applies does not hold on an
    rpm or flatpak host, and a renderer may run any of the three.
    """
    if not candidate or not installed:
        return False
    left, right = _release_of(candidate), _release_of(installed)
    if left == right:
        # 0.4.0 over the 0.4.0~dev3 that led up to it, never the reverse.
        return "~" in installed and "~" not in candidate
    return is_newer(left, right)


def _release_of(version: str) -> str:
    """The release a version belongs to: ``0.4.0~dev3+g1a2b3c4`` -> ``0.4.0``."""
    return version.split("~")[0].split("+")[0]

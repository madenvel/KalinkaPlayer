"""Background check for a newer Kalinka release.

A background task (started from the app lifecycle) reads the repo's public
``releases.atom`` feed once an hour — no token, not subject to the GitHub
API rate limit. ``GET /server/update`` serves the cached result only and
never does network I/O. With ``server.auto_upgrade`` on, the task also
fires the upgrade itself during quiet hours while playback is stopped.

Two release trains are watched, because one installer run covers both: the
``kalinka-v*`` app bundle, and the ``kalinka-renderer-v*`` renderer package
installed on this machine. Renderers on other boxes are upgraded by running
the installer there and are none of this module's business.

``PUT /server/upgrade`` must name the version the client is upgrading to;
it is rejected unless that matches the cached latest release, so a stale
banner can't fire an upgrade to a version nobody offered. The upgrade
itself is performed by root-side systemd units shipped in the deb —
kalinka-upgrade.path watches a trigger file the kalusr server touches and
runs upgrade.sh, which fetches the current published installer from
kalinkaplayer.com. This module only detects whether that machinery is
installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable

import httpx
from packaging.version import InvalidVersion, Version

from kalinka_plugin_sdk import paths

from .version import get_version

logger = logging.getLogger(__name__)

_BUNDLE_TAG_PREFIX = "kalinka-v"
_RENDERER_TAG_PREFIX = "kalinka-renderer-v"
_RENDERER_PACKAGE = "kalinka-renderer"
_TICK_INTERVAL = 3600  # check hourly; also guarantees ticks inside quiet hours
_QUIET_HOURS = range(3, 6)  # local time — auto-upgrade fires in [3:00, 6:00)

# Root-side upgrade machinery shipped by the deb. Deliberately not
# KALINKA_PREFIX-resolved: a dev fakeroot has no systemd watching its run
# dir, so upgrade must read as unsupported there even when a deb install
# also exists on the same machine.
_UPGRADE_SCRIPT = "/opt/kalinka/upgrade.sh"
_UPGRADE_PATH_UNIT = "/etc/systemd/system/kalinka-upgrade.path"


def _repo() -> str:
    # Same override the install script honours.
    return os.environ.get("KALINKA_REPO", "madenvel/KalinkaPlayer")


def upgrade_supported() -> bool:
    """True when PUT /server/upgrade can actually perform an upgrade."""
    return (
        paths.prefix() == "/"
        and os.path.isfile(_UPGRADE_SCRIPT)
        and os.path.isfile(_UPGRADE_PATH_UNIT)
    )


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def latest_release_version(feed_xml: str, prefix: str) -> str | None:
    """Version of the newest release tagged ``<prefix>*`` in a feed.

    Entries come newest-first; each entry's ``<id>`` ends in the release
    tag (``tag:github.com,2008:Repository/<id>/<tag>``). Tags of other
    families in the same repo (``kalinka-renderer-v*`` alongside
    ``kalinka-v*``, ``jamendo-ai-v*`` asset releases) are skipped, matching
    the selection in scripts/install-release.sh. Drafts never appear in the
    public feed.

    The feed carries one page of releases, so a family that has not been
    released in a long time can fall off it entirely and read as unknown —
    which costs an upgrade offer, never a wrong one.
    """
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as e:
        logger.warning("Cannot parse releases feed: %s", e)
        return None
    for entry in root.iter(f"{_ATOM_NS}entry"):
        tag = (entry.findtext(f"{_ATOM_NS}id") or "").rsplit("/", 1)[-1]
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


def is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        logger.warning("Cannot compare versions %r and %r", latest, current)
        return False


def installed_renderer_version() -> str | None:
    """Version of the renderer package installed here, None if there is none.

    dpkg only: the upgrade path installs debs, so a machine that can
    upgrade at all has dpkg. Anywhere else this reads as "no renderer
    here", which leaves the renderer out of the upgrade decision.
    """
    try:
        query = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", _RENDERER_PACKAGE],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Cannot query the installed renderer: %s", e)
        return None
    if query.returncode != 0:
        return None
    return query.stdout.strip() or None


def deb_is_newer(candidate: str, installed: str) -> bool:
    """Whether dpkg orders ``candidate`` above ``installed``.

    Renderer packages carry deb versions — ``0.2.0~dev3+g1a2b3c4`` sorts
    *below* the ``0.2.0`` it leads up to — which PEP 440 reads differently
    or not at all, so the question goes to the tool that will run the
    install.
    """
    try:
        compare = subprocess.run(
            ["dpkg", "--compare-versions", candidate, "gt", installed],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Cannot compare renderer versions: %s", e)
        return False
    return compare.returncode == 0


def request_upgrade() -> None:
    """Touch the trigger watched by kalinka-upgrade.path. Raises OSError."""
    trigger = Path(paths.run_dir()) / "upgrade-request"
    trigger.parent.mkdir(parents=True, exist_ok=True)
    trigger.touch()


def validate_upgrade_request(
    target: str,
    latest: str | None,
    current: str,
    renderer_stale: bool = False,
) -> str | None:
    """Reason a PUT /server/upgrade must be rejected, or None to proceed.

    The client echoes the bundle version it saw in GET /server/update; no
    known release, nothing left to upgrade, or a different release
    published since all reject rather than firing the installer again.
    ``renderer_stale`` keeps the request valid when the bundle is already
    current and only the local renderer is behind — one installer run
    covers both, so the echoed version stays the bundle's either way.
    """
    if not latest:
        return "No update available"
    if not is_newer(latest, current) and not renderer_stale:
        return "No update available"
    if target != latest:
        return (
            f"Requested version {target!r} is not the available "
            f"update {latest!r}"
        )
    return None


class UpdateChecker:
    """Hourly background lookup of what is published and what is installed.

    ``run()`` is started as a task from the app lifecycle; endpoints read
    the cached properties only. A transient fetch failure keeps the last
    known result (an available update must not vanish on a network blip),
    and so does a feed page that happens to carry no release of one
    family.
    """

    def __init__(self) -> None:
        self._latest: str | None = None
        self._latest_renderer: str | None = None
        self._installed_renderer: str | None = None
        self._renderer_stale = False
        self._last_auto_attempt: date | None = None

    @property
    def latest(self) -> str | None:
        """Latest known bundle release — None until a check succeeds."""
        return self._latest

    @property
    def latest_renderer(self) -> str | None:
        """Latest known renderer release — None until a check succeeds."""
        return self._latest_renderer

    @property
    def installed_renderer(self) -> str | None:
        """Renderer version installed here as of the last check."""
        return self._installed_renderer

    def update_available(self) -> bool:
        """Whether an installer run would bring anything newer to this box."""
        return (
            self.bundle_update_available() or self.renderer_update_available()
        )

    def bundle_update_available(self) -> bool:
        """Whether a newer app bundle than the running one is published."""
        return bool(self._latest and is_newer(self._latest, get_version()))

    def renderer_update_available(self) -> bool:
        """Whether the renderer installed here is behind its latest release.

        A machine with no renderer installed is not missing an update: the
        installer would be adding one, which is an install decision, not an
        upgrade.
        """
        return self._renderer_stale

    async def check_now(self) -> str | None:
        """Refresh both release trains and the local renderer version.

        Returns the latest bundle release, or None when the feed could not
        be read. The renderer verdict is settled here rather than on read,
        so asking for it costs no process and never blocks a request.
        """
        self._installed_renderer = installed_renderer_version()
        feed = await self._fetch()
        if feed is None:
            return None
        latest = latest_release_version(feed, _BUNDLE_TAG_PREFIX)
        renderer = latest_release_version(feed, _RENDERER_TAG_PREFIX)
        if latest:
            self._latest = latest
        if renderer:
            self._latest_renderer = renderer
        self._renderer_stale = bool(
            self._installed_renderer
            and self._latest_renderer
            and deb_is_newer(self._latest_renderer, self._installed_renderer)
        )
        return latest

    async def run(
        self,
        auto_upgrade_enabled: Callable[[], bool] = lambda: False,
        playback_stopped: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        """Hourly tick: refresh the release info and, when the (live-read)
        config toggle is on, fire the auto-upgrade in the quiet-hours
        window."""
        while True:
            await self.check_now()
            if auto_upgrade_enabled():
                await self.maybe_auto_upgrade(playback_stopped)
            await asyncio.sleep(_TICK_INTERVAL)

    async def maybe_auto_upgrade(
        self,
        playback_stopped: Callable[[], Awaitable[bool]] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Trigger the root-side upgrade during quiet hours, but never
        while something is playing — a later tick in the same window
        retries once playback stops.

        At most one attempt per day: a failed install (server still up
        next tick) retries the following night rather than hammering,
        and a successful one restarts the process anyway.
        """
        now = now or datetime.now()
        if now.hour not in _QUIET_HOURS:
            return
        if self._last_auto_attempt == now.date():
            return
        if not self.update_available():
            return
        if not upgrade_supported():
            return
        if playback_stopped is not None:
            try:
                if not await playback_stopped():
                    logger.info("Auto-upgrade postponed: playback active")
                    return
            except Exception as e:  # noqa: BLE001 — don't upgrade blind
                logger.warning("Auto-upgrade playback probe failed: %s", e)
                return
        self._last_auto_attempt = now.date()
        try:
            request_upgrade()
        except OSError as e:
            logger.error("Auto-upgrade trigger failed: %s", e)
            return
        logger.info(
            "Auto-upgrade requested (server %s, renderer %s)",
            self._latest if self.bundle_update_available() else "current",
            self._latest_renderer
            if self.renderer_update_available()
            else "current",
        )

    async def _fetch(self) -> str | None:
        """The repo's releases.atom document, or None when unreachable."""
        url = f"https://github.com/{_repo()}/releases.atom"
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=True
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Release feed lookup failed: %s", e)
            return None
        return response.text


#: Process-wide instance; run() is started from the server lifecycle.
checker = UpdateChecker()

"""Daily check for a newer Kalinka server release.

A background task (started from the app lifecycle) reads the repo's public
``releases.atom`` feed once a day — no token, not subject to the GitHub API
rate limit. ``GET /server/update`` serves the cached result only and never
does network I/O.

``PUT /server/upgrade`` must name the version the client is upgrading to;
it is rejected unless that matches the cached latest release and is newer
than the running version, so a stale banner or a retried request can't
fire a second upgrade. The upgrade itself is performed by root-side
systemd units shipped in the deb — kalinka-upgrade.path watches a trigger
file the kalusr server touches and runs upgrade.sh, which fetches the
current published installer from kalinkaplayer.com. This module only
detects whether that machinery is installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET

import httpx
from packaging.version import InvalidVersion, Version

from kalinka_plugin_sdk import paths

logger = logging.getLogger(__name__)

_TAG_PREFIX = "kalinka-v"
_CHECK_INTERVAL = 24 * 3600
_RETRY_INTERVAL = 3600  # retry sooner after a failed lookup

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


def latest_release_version(feed_xml: str) -> str | None:
    """Version of the newest ``kalinka-v*`` release in a releases.atom feed.

    Entries come newest-first; each entry's ``<id>`` ends in the release
    tag (``tag:github.com,2008:Repository/<id>/<tag>``). Foreign tag
    families in the same repo (e.g. ``jamendo-ai-v*`` asset releases) are
    skipped, matching the selection in scripts/install-release.sh. Drafts
    never appear in the public feed.
    """
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as e:
        logger.warning("Cannot parse releases feed: %s", e)
        return None
    for entry in root.iter(f"{_ATOM_NS}entry"):
        tag = (entry.findtext(f"{_ATOM_NS}id") or "").rsplit("/", 1)[-1]
        if tag.startswith(_TAG_PREFIX):
            return tag[len(_TAG_PREFIX) :]
    return None


def is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        logger.warning("Cannot compare versions %r and %r", latest, current)
        return False


def validate_upgrade_request(
    target: str, latest: str | None, current: str
) -> str | None:
    """Reason a PUT /server/upgrade must be rejected, or None to proceed.

    The client echoes the version it saw in GET /server/update; no known
    update, already upgraded, or a different release published since all
    reject rather than firing the installer again.
    """
    if not latest or not is_newer(latest, current):
        return "No update available"
    if target != latest:
        return (
            f"Requested version {target!r} is not the available "
            f"update {latest!r}"
        )
    return None


class UpdateChecker:
    """Once-a-day background lookup of the latest published release.

    ``run()`` is started as a task from the app lifecycle; endpoints read
    ``latest`` only. A transient fetch failure keeps the last known
    result (an available update must not vanish on a network blip) and
    retries on the shorter interval.
    """

    def __init__(self) -> None:
        self._latest: str | None = None

    @property
    def latest(self) -> str | None:
        """Latest known release version — None until a check succeeds."""
        return self._latest

    async def check_now(self) -> str | None:
        latest = await self._fetch()
        if latest:
            self._latest = latest
        return latest

    async def run(self) -> None:
        while True:
            found = await self.check_now()
            await asyncio.sleep(_CHECK_INTERVAL if found else _RETRY_INTERVAL)

    async def _fetch(self) -> str | None:
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
        return latest_release_version(response.text)


#: Process-wide instance; run() is started from the server lifecycle.
checker = UpdateChecker()

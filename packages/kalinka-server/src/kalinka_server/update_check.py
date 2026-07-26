"""Check GitHub for a newer Kalinka server release.

Backs ``GET /server/update``: the app asks once at startup whether a newer
``kalinka-v*`` release exists and shows an upgrade banner (dismissal is
client-side). The lookup reads the public ``releases.atom`` feed — the
machine-readable form of the repo's releases page — which needs no token
and is not subject to the anonymous API rate limit, so it works out of
the box on any install. Results are still cached per process to keep the
check to roughly one fetch per app start.

The actual upgrade (``PUT /server/upgrade``) is performed by root-side
systemd units shipped in the deb — kalinka-upgrade.path watches a trigger
file the kalusr server touches and runs upgrade.sh, which fetches the
current published installer from kalinkaplayer.com. This module only
detects whether that machinery is installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import xml.etree.ElementTree as ET

import httpx
from packaging.version import InvalidVersion, Version

from kalinka_plugin_sdk import paths

logger = logging.getLogger(__name__)

_TAG_PREFIX = "kalinka-v"
_SUCCESS_TTL = 15 * 60  # seconds a successful lookup is served from cache
_FAILURE_TTL = 60  # retry failed lookups sooner, but never in a tight loop

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
        logger.warning(
            "Cannot compare versions %r and %r", latest, current
        )
        return False


class UpdateChecker:
    """TTL-cached lookup of the latest published release version."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: str | None = None
        self._expires = 0.0

    async def latest_version(self) -> str | None:
        """Latest release version, or None when the lookup fails."""
        async with self._lock:
            now = time.monotonic()
            if now < self._expires:
                return self._cached
            self._cached = await self._fetch()
            self._expires = now + (
                _SUCCESS_TTL if self._cached else _FAILURE_TTL
            )
            return self._cached

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


#: Process-wide instance backing GET /server/update.
checker = UpdateChecker()

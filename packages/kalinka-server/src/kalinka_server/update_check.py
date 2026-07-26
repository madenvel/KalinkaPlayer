"""Check GitHub for a newer Kalinka server release.

Backs ``GET /server/update``: the app asks once at startup whether a newer
``kalinka-v*`` release exists and shows an upgrade banner (dismissal is
client-side). Results are cached per process so several clients don't
hammer the anonymous GitHub API (60 req/hr per source IP).

The actual upgrade (``PUT /server/upgrade``) is performed by root-side
systemd units shipped in the deb — kalinka-upgrade.path watches a trigger
file the kalusr server touches and runs the bundled install-release.sh.
This module only detects whether that machinery is installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

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
_UPGRADE_SCRIPT = "/opt/kalinka/install-release.sh"
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


def latest_release_version(releases: list) -> str | None:
    """Version of the newest non-draft, non-prerelease ``kalinka-v*`` release.

    The GitHub list endpoint returns newest-first; mirrors the selection
    logic in scripts/install-release.sh so the check and the installer
    always agree on what "latest" means.
    """
    for release in releases:
        if not isinstance(release, dict):
            continue
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name", ""))
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
        url = f"https://api.github.com/repos/{_repo()}/releases?per_page=30"
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                releases = response.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Release lookup failed: %s", e)
            return None
        if not isinstance(releases, list):
            logger.warning("Unexpected releases payload from GitHub")
            return None
        return latest_release_version(releases)


#: Process-wide instance backing GET /server/update.
checker = UpdateChecker()

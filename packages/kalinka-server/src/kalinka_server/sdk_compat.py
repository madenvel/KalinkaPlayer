"""Startup guard: refuse to run with an incompatible kalinka-plugin-sdk.

Compatibility is declared in exactly one place — this server's
``kalinka-plugin-sdk`` dependency in ``pyproject.toml`` (``>=2,<3``). We read
that requirement back from installed package metadata and check the installed
SDK against it, so there is no second copy of the supported range to keep in
sync.

Plugins pin the same SDK major, so an SDK that satisfies the server implies the
plugins are within the supported major as well. This is the runtime backstop
for the cases dependency resolution can't catch (``pip install --no-deps``,
``dpkg --force-depends``, an in-place SDK upgrade to a different major).

See RELEASING.md for the versioning policy.
"""

import logging
from importlib.metadata import PackageNotFoundError, requires
from importlib.metadata import version as dist_version

from packaging.requirements import Requirement

logger = logging.getLogger(__name__.split(".")[-1])

SERVER_DIST = "kalinka-server"
SDK_DIST = "kalinka-plugin-sdk"


class IncompatibleSDKError(RuntimeError):
    """Raised when the installed SDK does not satisfy the server's requirement."""


def _server_sdk_requirement() -> Requirement | None:
    """Return the server's declared ``kalinka-plugin-sdk`` requirement, if any."""
    try:
        declared = requires(SERVER_DIST) or []
    except PackageNotFoundError:
        # Running from a source tree that was never installed as a dist.
        return None
    for raw in declared:
        req = Requirement(raw)
        # Skip requirements gated by a marker (e.g. `; extra == "dev"`); only the
        # unconditional runtime requirement is the one we enforce.
        if req.name == SDK_DIST and not req.marker:
            return req
    return None


def check_sdk_compatibility() -> None:
    """Raise :class:`IncompatibleSDKError` if the installed SDK is incompatible.

    No-ops with a warning when the requirement or the SDK version can't be
    determined (e.g. an uninstalled source checkout), so development isn't
    blocked.
    """
    req = _server_sdk_requirement()
    if req is None:
        logger.warning(
            "Could not determine the required %s version; skipping SDK compatibility check",
            SDK_DIST,
        )
        return

    try:
        installed = dist_version(SDK_DIST)
    except PackageNotFoundError as exc:
        raise IncompatibleSDKError(
            f"{SDK_DIST} is required ({req.specifier}) but is not installed"
        ) from exc

    # prereleases=True so local dev builds (e.g. 1.0.1.dev5+...) are accepted.
    if not req.specifier.contains(installed, prereleases=True):
        raise IncompatibleSDKError(
            f"Incompatible {SDK_DIST} {installed}: this server requires "
            f"{SDK_DIST}{req.specifier}. Install a matching SDK "
            f"(and rebuild plugins for this major)."
        )

    logger.info(
        "SDK compatibility OK: %s %s satisfies %s", SDK_DIST, installed, req.specifier
    )

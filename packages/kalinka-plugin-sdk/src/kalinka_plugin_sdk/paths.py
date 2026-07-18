"""Filesystem layout for Kalinka, rooted at an optional install prefix.

In production the server and its data live at fixed FHS locations
(``/etc/kalinka``, ``/var/lib/kalinka``, …). For running from source without
root — or installing into a sandbox — every one of those absolute paths is
resolved against ``$KALINKA_PREFIX`` instead of assuming ``/``.

    KALINKA_PREFIX unset / "/"   →  /etc/kalinka, /var/lib/kalinka, …   (prod)
    KALINKA_PREFIX=~/kalinka     →  ~/kalinka/etc/kalinka, ~/kalinka/var/lib/kalinka, …

The default is ``/``, so production behaviour is unchanged: a prefixed path is
``os.path.join`` of the prefix and the same relative tail. Both the server and
plugins import these helpers so a single env var relocates the whole tree.

The prefix is read on every call (not cached) so tests and the dev launcher can
set it before importing consumers without import-order surprises.
"""

from __future__ import annotations

import os

#: Relative tails (no leading slash, so they compose under any prefix).
_ETC = "etc/kalinka"
_STATE = "var/lib/kalinka"
_LOG = "var/log/kalinka"
_RUN = "run/kalinka"
_CACHE = "var/cache/kalinka"
_MEDIA = "srv/kalinka/music"
_WEB_UI = "usr/share/kalinka-web"


def prefix() -> str:
    """Install root that all system paths resolve against (``$KALINKA_PREFIX``,
    default ``/``)."""
    return os.environ.get("KALINKA_PREFIX", "/")


def _under(tail: str) -> str:
    return os.path.join(prefix(), tail)


def etc_dir() -> str:
    """Config directory — ``<prefix>/etc/kalinka``."""
    return _under(_ETC)


def state_dir() -> str:
    """Persistent state — ``<prefix>/var/lib/kalinka`` (db, state, installs)."""
    return _under(_STATE)


def log_dir() -> str:
    """Log directory — ``<prefix>/var/log/kalinka``."""
    return _under(_LOG)


def run_dir() -> str:
    """Runtime directory — ``<prefix>/run/kalinka`` (restart trigger, sockets)."""
    return _under(_RUN)


def cache_dir() -> str:
    """Cache directory — ``<prefix>/var/cache/kalinka`` (artwork, numba)."""
    return _under(_CACHE)


def media_dir() -> str:
    """Default music drop-off — ``<prefix>/srv/kalinka/music`` (under /srv, FHS
    "served data", so the deb can provision it world-writable)."""
    return _under(_MEDIA)


def web_ui_dir() -> str:
    """Browser player bundle — ``<prefix>/usr/share/kalinka-web`` (installed by
    the optional kalinka-web package; served at ``/`` when present)."""
    return _under(_WEB_UI)

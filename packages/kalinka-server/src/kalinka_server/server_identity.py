"""Stable Core identity, persisted at <state_dir>/server_id.

Renderers report the owner of their active playback session by server_id, so
this must survive a restart, a port change and a rename — anything less and a
recovered Core cannot recognise its own orphaned session.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from kalinka_plugin_sdk import paths

logger = logging.getLogger(__name__.split(".")[-1])

_server_id: str | None = None


def _read(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        logger.warning("Ignoring malformed server id at %s", path)
        return None


def get_server_id() -> str:
    global _server_id
    if _server_id is not None:
        return _server_id

    path = Path(paths.state_dir()) / "server_id"
    existing = _read(path)
    if existing is not None:
        _server_id = existing
        return _server_id

    _server_id = str(uuid.uuid4())
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(_server_id + "\n")
        os.replace(tmp, path)
        logger.info("Generated server id %s at %s", _server_id, path)
    except OSError as exc:
        logger.warning(
            "Could not persist server id at %s (%s); using an ephemeral id",
            path,
            exc,
        )
    return _server_id

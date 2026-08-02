"""Playback sessions: a Core's exclusive claim on a renderer.

The Core mints the session id; both sides keep it in memory only, so a crash on
either side ends the session. A dropped link only suspends it — the renderer
keeps playing and the session is rebound when it returns. Whatever the two
sides then disagree about is settled by reconcile() at the next Hello:

    renderer reports        Core holds         outcome
    ----------------------------------------------------------------------
    our session S           S                  resume (rebind)
    our session S           nothing            SessionClose(STALE) to renderer
    another Core's session  -                  not ours; ignored
    nothing                 session T          T closed, RENDERER_RESTARTED

Reopening after a close is the owner's decision, never the pool's.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from .renderer_registry import RendererRegistry, RendererStatus

logger = logging.getLogger(__name__.split(".")[-1])

DEFAULT_OPEN_TIMEOUT_S = 5.0


class SessionState(str, Enum):
    OPENING = "opening"
    ACTIVE = "active"
    SUSPENDED = "suspended"  # renderer offline; session kept for its return
    CLOSED = "closed"


class CloseReason(str, Enum):
    CLOSED_BY_SERVER = "closed_by_server"
    STALE = "stale"
    SHUTDOWN = "shutdown"
    RENDERER_RESTARTED = "renderer_restarted"
    RENDERER_LOST = "renderer_lost"
    RENDERER_ERROR = "renderer_error"
    OPEN_FAILED = "open_failed"


# The rest describe a renderer that already lost the session; telling it would
# be addressed to a session it no longer has.
_WIRE_CLOSE_REASONS = {
    CloseReason.CLOSED_BY_SERVER,
    CloseReason.STALE,
    CloseReason.SHUTDOWN,
}


class RendererUnavailable(Exception):
    """The renderer is not connected, so no session can be opened."""


class RendererBusy(Exception):
    def __init__(self, message: str, owner_server_id: str = ""):
        super().__init__(message)
        self.owner_server_id = owner_server_id


class SessionOpenFailed(Exception):
    """The renderer refused the session for a reason other than busy."""


@dataclass
class _OpenOutcome:
    accepted: bool
    busy: bool
    detail: str
    owner_server_id: str


def _invoke(callback, session: "PlaybackSession", reason: CloseReason) -> None:
    try:
        result = callback(session, reason)
    except Exception:
        logger.exception("Session close callback failed")
        return
    if inspect.isawaitable(result):
        asyncio.create_task(result)


class PlaybackSession:
    def __init__(
        self,
        pool: "SessionPool",
        session_id: str,
        renderer_id: str,
        ws_session: Any,
    ):
        self.session_id = session_id
        self.renderer_id = renderer_id
        self.state = SessionState.OPENING
        self.opened_at = time.time()
        self.close_reason: Optional[CloseReason] = None
        self._pool = pool
        self._ws = ws_session
        self._open_future: Optional[asyncio.Future] = None
        self._callbacks: list[Callable] = []

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def on_closed(self, callback: Callable[["PlaybackSession", CloseReason], Any]) -> None:
        if self.state is SessionState.CLOSED:
            _invoke(callback, self, self.close_reason or CloseReason.CLOSED_BY_SERVER)
            return
        self._callbacks.append(callback)

    async def close(self, reason: CloseReason = CloseReason.CLOSED_BY_SERVER) -> None:
        """Idempotent, and never waits on the renderer's acknowledgement."""
        if self.state is SessionState.CLOSED:
            return
        ws = self._ws
        self._finish(reason)
        if ws is not None and reason in _WIRE_CLOSE_REASONS:
            await _send_close(ws, self.session_id, reason)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "renderer_id": self.renderer_id,
            "state": self.state.value,
            "opened_at": self.opened_at,
            "connected": self.connected,
        }

    def _rebind(self, ws_session: Any) -> None:
        self._ws = ws_session
        self.state = SessionState.ACTIVE

    def _suspend(self) -> None:
        self._ws = None
        self.state = SessionState.SUSPENDED

    def _finish(self, reason: CloseReason) -> None:
        if self.state is SessionState.CLOSED:
            return
        self.state = SessionState.CLOSED
        self.close_reason = reason
        self._ws = None
        self._pool._forget(self)
        if self._open_future is not None and not self._open_future.done():
            self._open_future.set_exception(
                SessionOpenFailed(f"session closed while opening ({reason.value})")
            )
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            _invoke(callback, self, reason)


async def _send_close(ws_session: Any, session_id: str, reason: CloseReason) -> None:
    try:
        await ws_session.send_session_close(session_id, reason)
    except Exception as exc:
        logger.debug("Could not send SessionClose for %s: %s", session_id, exc)


class SessionPool:
    """At most one open session per renderer, keyed by renderer_id."""

    def __init__(
        self,
        registry: RendererRegistry,
        server_id: str,
        open_timeout_s: float = DEFAULT_OPEN_TIMEOUT_S,
    ):
        self._registry = registry
        self.server_id = server_id
        self._open_timeout_s = open_timeout_s
        self._sessions: dict[str, PlaybackSession] = {}

    def get(self, renderer_id: str) -> Optional[PlaybackSession]:
        return self._sessions.get(renderer_id)

    def list(self) -> list[dict]:
        return [session.to_dict() for session in self._sessions.values()]

    async def open(self, renderer_id: str) -> PlaybackSession:
        record = self._registry.get(renderer_id)
        if (
            record is None
            or record.status is not RendererStatus.CONNECTED
            or record.session is None
        ):
            raise RendererUnavailable(f"renderer {renderer_id} is not connected")
        if renderer_id in self._sessions:
            raise RendererBusy(
                f"renderer {renderer_id} already has a session with this Core",
                owner_server_id=self.server_id,
            )

        ws = record.session
        session = PlaybackSession(self, str(uuid.uuid4()), renderer_id, ws)
        self._sessions[renderer_id] = session
        session._open_future = asyncio.get_running_loop().create_future()

        try:
            await ws.send_session_open(session.session_id)
            outcome = await asyncio.wait_for(
                session._open_future, self._open_timeout_s
            )
        except asyncio.TimeoutError:
            session._finish(CloseReason.OPEN_FAILED)
            # It may have accepted and lost the reply; don't leave it claimed.
            await _send_close(ws, session.session_id, CloseReason.CLOSED_BY_SERVER)
            raise
        except SessionOpenFailed:
            raise
        except Exception:
            session._finish(CloseReason.OPEN_FAILED)
            raise

        if not outcome.accepted:
            session._finish(CloseReason.OPEN_FAILED)
            if outcome.busy:
                raise RendererBusy(
                    f"renderer {renderer_id} is in use by another Core",
                    owner_server_id=outcome.owner_server_id,
                )
            raise SessionOpenFailed(outcome.detail or "renderer refused the session")

        session.state = SessionState.ACTIVE
        logger.info("Opened session %s on renderer %s", session.session_id, renderer_id)
        return session

    async def reconcile(
        self,
        *,
        renderer_id: str,
        reported_session_id: str,
        reported_owner_server_id: str,
        ws_session: Any,
    ) -> None:
        """Settle renderer-reported session state against the pool, at Hello."""
        session = self._sessions.get(renderer_id)
        ours = bool(reported_session_id) and reported_owner_server_id == self.server_id

        if ours and session is not None and session.session_id == reported_session_id:
            session._rebind(ws_session)
            logger.info(
                "Resumed session %s on renderer %s", reported_session_id, renderer_id
            )
            return

        if ours:
            logger.info(
                "Renderer %s reports session %s which this Core no longer has; "
                "closing it as stale",
                renderer_id,
                reported_session_id,
            )
            await _send_close(ws_session, reported_session_id, CloseReason.STALE)

        if session is not None:
            logger.info(
                "Renderer %s returned without session %s; closing it",
                renderer_id,
                session.session_id,
            )
            session._finish(CloseReason.RENDERER_RESTARTED)

    def handle_open_result(
        self,
        renderer_id: str,
        *,
        session_id: str,
        accepted: bool,
        busy: bool,
        detail: str,
        owner_server_id: str,
    ) -> None:
        session = self._sessions.get(renderer_id)
        if (
            session is None
            or session.session_id != session_id
            or session._open_future is None
            or session._open_future.done()
        ):
            logger.debug(
                "Ignoring open result for unknown session %s from %s",
                session_id,
                renderer_id,
            )
            return
        session._open_future.set_result(
            _OpenOutcome(accepted, busy, detail, owner_server_id)
        )

    def handle_closed(
        self,
        renderer_id: str,
        *,
        session_id: str,
        renderer_error: bool,
        detail: str,
    ) -> None:
        session = self._sessions.get(renderer_id)
        if session is None or session.session_id != session_id:
            return
        if renderer_error:
            logger.warning(
                "Renderer %s ended session %s: %s", renderer_id, session_id, detail
            )
            session._finish(CloseReason.RENDERER_ERROR)

    def suspend(self, renderer_id: str, ws_session: Any) -> None:
        """The connection carrying this session dropped."""
        session = self._sessions.get(renderer_id)
        if session is None or session._ws is not ws_session:
            return  # stale disconnect of a replaced connection
        if session.state is SessionState.OPENING:
            session._finish(CloseReason.OPEN_FAILED)
            return
        session._suspend()
        logger.info(
            "Session %s suspended; renderer %s went offline",
            session.session_id,
            renderer_id,
        )

    def handle_renderer_removed(self, renderer_id: str) -> None:
        session = self._sessions.get(renderer_id)
        if session is not None:
            logger.info(
                "Renderer %s is gone; closing session %s",
                renderer_id,
                session.session_id,
            )
            session._finish(CloseReason.RENDERER_LOST)

    async def shutdown(self) -> None:
        for session in list(self._sessions.values()):
            await session.close(CloseReason.SHUTDOWN)

    def _forget(self, session: PlaybackSession) -> None:
        if self._sessions.get(session.renderer_id) is session:
            del self._sessions[session.renderer_id]

"""Renderer WebSocket endpoint: one binary protobuf Envelope per message.

Receiver task pushes parsed envelopes into a bounded queue; processor task
drains it, so message handling never stalls the socket read.
"""

import asyncio
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect
from google.protobuf.message import DecodeError

from .config_model import KalinkaConfig
from .renderer_proto import renderer_pb2 as pb
from .renderer_registry import RendererRegistry
from .renderer_sessions import CloseReason, SessionPool
from .server_identity import get_server_id
from .version import get_rest_api_version, get_version

logger = logging.getLogger(__name__.split(".")[-1])

# Renderer protocol version this Core speaks; independent of REST_API_VERSION.
PROTOCOL_VERSION = 1

INBOX_SIZE = 256

_CLOSE_REASON_TO_PB = {
    CloseReason.STALE: pb.SessionClose.REASON_STALE,
    CloseReason.CLOSED_BY_SERVER: pb.SessionClose.REASON_CLOSED_BY_SERVER,
    CloseReason.SHUTDOWN: pb.SessionClose.REASON_CLOSED_BY_SERVER,
}


def _kind_name(kind: int) -> str:
    return pb.RendererKind.Name(kind).removeprefix("RENDERER_KIND_").lower()


class RendererSession:
    """Send side of one renderer connection; held by the registry."""

    def __init__(self, websocket: WebSocket):
        self._websocket = websocket
        self._out_id = 0
        self._send_lock = asyncio.Lock()

    def _envelope(self) -> pb.Envelope:
        self._out_id += 1
        env = pb.Envelope()
        env.message_id = self._out_id
        return env

    async def _send(self, env: pb.Envelope) -> None:
        async with self._send_lock:
            await self._websocket.send_bytes(env.SerializeToString())

    async def send_welcome(self, config: KalinkaConfig) -> None:
        env = self._envelope()
        welcome = env.welcome
        welcome.protocol_version = PROTOCOL_VERSION
        welcome.server_id = get_server_id()
        welcome.server_name = config.server.service_name
        welcome.server_version = get_version()
        welcome.api_version = get_rest_api_version()
        welcome.server_time_unix_ms = int(time.time() * 1000)
        await self._send(env)

    async def send_session_open(self, session_id: str) -> None:
        env = self._envelope()
        env.session_open.session_id = session_id
        await self._send(env)

    async def send_session_close(self, session_id: str, reason) -> None:
        env = self._envelope()
        env.session_close.session_id = session_id
        env.session_close.reason = _CLOSE_REASON_TO_PB.get(
            reason, pb.SessionClose.REASON_CLOSED_BY_SERVER
        )
        await self._send(env)

    async def send_goodbye(self, reason, detail: str) -> None:
        env = self._envelope()
        env.goodbye.reason = reason
        env.goodbye.detail = detail
        await self._send(env)

    async def replace(self) -> None:
        """Retire this session in favour of a newer connection."""
        try:
            await self.send_goodbye(
                pb.Goodbye.REASON_REPLACED,
                "another connection registered this renderer_id",
            )
            await self._websocket.close()
        except Exception:
            pass  # the old link may already be dead


async def handle_renderer_connection(
    websocket: WebSocket,
    config: KalinkaConfig,
    registry: RendererRegistry,
    sessions: SessionPool,
):
    await websocket.accept()
    session = RendererSession(websocket)
    inbox: asyncio.Queue[pb.Envelope] = asyncio.Queue(maxsize=INBOX_SIZE)

    registered_id: str | None = None
    clean_goodbye = False
    renderer_desc = "unregistered renderer"

    async def receiver():
        while True:
            data = await websocket.receive_bytes()
            env = pb.Envelope()
            try:
                env.ParseFromString(data)
            except DecodeError:
                logger.warning(
                    "Dropping unparseable %d-byte message from %s",
                    len(data),
                    renderer_desc,
                )
                await session.send_goodbye(
                    pb.Goodbye.REASON_MALFORMED, "unparseable envelope"
                )
                return
            await inbox.put(env)

    async def processor():
        nonlocal registered_id, clean_goodbye, renderer_desc
        while True:
            env = await inbox.get()
            payload = env.WhichOneof("payload")
            if payload == "hello":
                hello = env.hello
                renderer_desc = (
                    f"'{hello.friendly_name}' (id={hello.renderer_id})"
                )
                versions = hello.protocol_versions
                if not versions.min <= PROTOCOL_VERSION <= versions.max:
                    logger.warning(
                        "Renderer %s speaks protocol %d-%d, server speaks "
                        "%d; rejecting",
                        renderer_desc,
                        versions.min,
                        versions.max,
                        PROTOCOL_VERSION,
                    )
                    await session.send_goodbye(
                        pb.Goodbye.REASON_VERSION_UNSUPPORTED,
                        f"server speaks protocol version {PROTOCOL_VERSION}",
                    )
                    return
                registry.register(
                    renderer_id=hello.renderer_id,
                    instance_id=hello.instance_id,
                    friendly_name=hello.friendly_name,
                    software_version=hello.software_version,
                    kind=_kind_name(hello.kind),
                    platform={
                        "os": hello.platform.os,
                        "os_version": hello.platform.os_version,
                        "arch": hello.platform.arch,
                        "hostname": hello.platform.hostname,
                        "audio_backend": hello.platform.audio_backend,
                    },
                    session=session,
                )
                registered_id = hello.renderer_id
                await session.send_welcome(config)
                await sessions.reconcile(
                    renderer_id=hello.renderer_id,
                    reported_session_id=hello.active_session_id,
                    reported_owner_server_id=hello.session_owner_server_id,
                    ws_session=session,
                )
            elif payload == "session_open_result":
                result = env.session_open_result
                sessions.handle_open_result(
                    registered_id or "",
                    session_id=result.session_id,
                    accepted=result.accepted,
                    busy=result.error == pb.SessionOpenResult.ERROR_BUSY,
                    detail=result.detail,
                    owner_server_id=result.owner_server_id,
                )
            elif payload == "session_closed":
                closed = env.session_closed
                sessions.handle_closed(
                    registered_id or "",
                    session_id=closed.session_id,
                    renderer_error=closed.reason
                    == pb.SessionClosed.REASON_RENDERER_ERROR,
                    detail=closed.detail,
                )
            elif payload == "goodbye":
                clean_goodbye = True
                logger.info(
                    "Renderer %s said goodbye (reason %s)",
                    renderer_desc,
                    pb.Goodbye.Reason.Name(env.goodbye.reason),
                )
            else:
                logger.debug(
                    "Ignoring renderer message with payload %r", payload
                )

    receive_task = asyncio.create_task(receiver())
    process_task = asyncio.create_task(processor())
    try:
        await asyncio.wait(
            {receive_task, process_task}, return_when=asyncio.FIRST_COMPLETED
        )
    except asyncio.CancelledError:
        raise
    finally:
        receive_task.cancel()
        process_task.cancel()
        await asyncio.gather(receive_task, process_task, return_exceptions=True)
        for task in (receive_task, process_task):
            exc = task.exception() if task.done() and not task.cancelled() else None
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.error("Renderer %s connection error: %s", renderer_desc, exc)
        if registered_id is not None:
            registry.disconnect(registered_id, session, clean=clean_goodbye)
            sessions.suspend(registered_id, session)
        else:
            logger.info("Renderer connection closed before registration")
        try:
            await websocket.close()
        except Exception:
            pass

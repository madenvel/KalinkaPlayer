"""WebSocket endpoint for native renderers (binary Protocol Buffers).

One protobuf ``Envelope`` per binary WebSocket message; schema lives in
``packages/kalinka-renderer/proto`` (bindings committed under
``renderer_proto/``). MVP scope: accept a ``Hello``, answer with ``Welcome``
(or ``Goodbye`` on version mismatch / garbage), then hold the connection open
until the renderer disconnects. See docs/native-renderer-design.md.
"""

import logging
import time

from fastapi import WebSocket, WebSocketDisconnect
from google.protobuf.message import DecodeError

from .config_model import KalinkaConfig
from .renderer_proto import renderer_pb2 as pb
from .version import get_rest_api_version, get_version

logger = logging.getLogger(__name__.split(".")[-1])

# Renderer protocol version this Core speaks. Independent of REST_API_VERSION.
PROTOCOL_VERSION = 1


async def handle_renderer_connection(websocket: WebSocket, config: KalinkaConfig):
    await websocket.accept()

    out_id = 0

    def envelope() -> pb.Envelope:
        nonlocal out_id
        out_id += 1
        env = pb.Envelope()
        env.message_id = out_id
        return env

    async def send_goodbye(reason, detail: str):
        env = envelope()
        env.goodbye.reason = reason
        env.goodbye.detail = detail
        await websocket.send_bytes(env.SerializeToString())

    renderer_desc = "unregistered renderer"
    try:
        while True:
            data = await websocket.receive_bytes()
            env = pb.Envelope()
            try:
                env.ParseFromString(data)
            except DecodeError:
                logger.warning(
                    "Dropping unparseable %d-byte renderer message", len(data)
                )
                await send_goodbye(
                    pb.Goodbye.REASON_MALFORMED, "unparseable envelope"
                )
                return

            payload = env.WhichOneof("payload")
            if payload == "hello":
                hello = env.hello
                renderer_desc = (
                    f"'{hello.friendly_name}' (id={hello.renderer_id})"
                )
                versions = hello.protocol_versions
                if not versions.min <= PROTOCOL_VERSION <= versions.max:
                    logger.warning(
                        "Renderer %s speaks protocol %d-%d, server speaks %d; "
                        "rejecting",
                        renderer_desc,
                        versions.min,
                        versions.max,
                        PROTOCOL_VERSION,
                    )
                    await send_goodbye(
                        pb.Goodbye.REASON_VERSION_UNSUPPORTED,
                        f"server speaks protocol version {PROTOCOL_VERSION}",
                    )
                    return
                logger.info(
                    "Renderer registered: %s, %s %s on %s (%s/%s)",
                    renderer_desc,
                    hello.software_version,
                    pb.RendererKind.Name(hello.kind),
                    hello.platform.hostname,
                    hello.platform.os,
                    hello.platform.arch,
                )
                out = envelope()
                welcome = out.welcome
                welcome.protocol_version = PROTOCOL_VERSION
                welcome.server_name = config.server.service_name
                welcome.server_version = get_version()
                welcome.api_version = get_rest_api_version()
                welcome.server_time_unix_ms = int(time.time() * 1000)
                await websocket.send_bytes(out.SerializeToString())
            elif payload == "goodbye":
                logger.info(
                    "Renderer %s said goodbye (reason %s)",
                    renderer_desc,
                    pb.Goodbye.Reason.Name(env.goodbye.reason),
                )
                # The renderer closes the socket next; fall through to the
                # disconnect exception rather than racing it with a close.
            else:
                logger.debug(
                    "Ignoring renderer message with payload %r", payload
                )
    except WebSocketDisconnect:
        logger.info("Renderer %s disconnected", renderer_desc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

"""Serve library audio over HTTP, so renderers on other machines can play it.

``file://`` links only resolve in-process, but streams are fetched by the
renderer, wherever it runs. This listener is the seed of the future standalone
media server: tracks are addressed by id — a path never appears in a URL and
the folder structure is not exposed — and links point at this listener's own
host:port even when it shares a machine with kalinka-server, so the URL shape
survives the split.

The server itself is a bare asyncio protocol handler, not a web framework:
one GET route, single-range requests, and the body goes out through kernel
``sendfile`` (zero copy). That covers the renderer's client exactly — curl
asking ``Range: bytes=<offset>-`` and reading the stream size out of
``Content-Range`` (see AudioGraphHttpStream) — with seek and content size
intact. The port is ephemeral by design: bound fresh on every start, and
links carry it because they are minted per play, never stored.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import socket
from typing import Optional

from .utils.name_utils import path_within_roots

logger = logging.getLogger(__name__.split(".")[-1])

mimetypes.add_type("audio/flac", ".flac")

_READ_TIMEOUT_S = 30.0
_SENDFILE_FALLBACK_CHUNK = 256 * 1024


def default_route_ip() -> str:
    """The machine's LAN address, found without sending traffic (a UDP socket
    "connect" only selects the route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _parse_range(value: str, size: int) -> tuple[str, int, int]:
    """-> (kind, offset, count); kind is "range", "unsatisfiable" or "full".

    "full" covers everything malformed — HTTP allows ignoring an invalid
    Range header and serving the whole file.
    """
    full = ("full", 0, size)
    if not value.startswith("bytes="):
        return full
    spec = value[len("bytes="):].strip()
    if "," in spec or "-" not in spec:
        return full
    first, _, last = spec.partition("-")
    try:
        if first == "":
            suffix = int(last)  # suffix: last N bytes
            if suffix <= 0:
                return full
            offset, end = max(0, size - suffix), size - 1
        else:
            offset = int(first)
            end = int(last) if last else size - 1
            if offset < 0 or (last and end < offset):
                return full
    except ValueError:
        return full
    if offset >= size:
        return "unsatisfiable", 0, 0
    return "range", offset, min(end, size - 1) - offset + 1


class MediaHttpServer:
    def __init__(self, db_manager, music_folders: list[str]):
        self._db = db_manager
        self._roots = music_folders
        self._bound_port: Optional[int] = None
        self._server: Optional[asyncio.Server] = None
        self._connections: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        return self._bound_port or 0

    def url_for(self, track_id: str) -> str:
        """Formed per call: the LAN address may change between plays."""
        return f"http://{default_route_ip()}:{self.port}/audio/{track_id}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, host="0.0.0.0", port=0
        )
        self._bound_port = self._server.sockets[0].getsockname()[1]
        logger.info("Serving library audio on port %d", self._bound_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._connections):
            task.cancel()
        if self._connections:
            await asyncio.gather(*self._connections, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        self._server = None

    # ------------------------------------------------------------------ serving
    async def _handle_connection(self, reader, writer) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._connections.add(task)
        try:
            while True:
                head = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), _READ_TIMEOUT_S
                )
                if not await self._respond(writer, head):
                    break
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            asyncio.TimeoutError,
            ConnectionError,
        ):
            pass
        finally:
            self._connections.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass

    async def _respond(self, writer, head: bytes) -> bool:
        """Answer one request; returns whether to keep the connection open."""
        try:
            request_line, *header_lines = head.decode("latin-1").split("\r\n")
            method, target, version = request_line.split(" ")
        except ValueError:
            await self._send_error(writer, "400 Bad Request")
            return False
        headers = {}
        for line in header_lines:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()

        keep_alive = (
            headers.get("connection", "").lower() != "close"
            and version != "HTTP/1.0"
        )

        if method not in ("GET", "HEAD"):
            await self._send_error(writer, "405 Method Not Allowed")
            return False

        path = self._resolve(target)
        if path is None:
            await self._send_error(writer, "404 Not Found", keep_alive)
            return keep_alive
        size = os.stat(path).st_size

        offset, count, status = 0, size, "200 OK"
        range_header = headers.get("range")
        if range_header is not None:
            kind, offset, count = _parse_range(range_header, size)
            if kind == "unsatisfiable":
                await self._send_error(
                    writer,
                    "416 Range Not Satisfiable",
                    keep_alive,
                    extra=f"Content-Range: bytes */{size}\r\n",
                )
                return keep_alive
            if kind == "range":
                status = "206 Partial Content"

        response = [
            f"HTTP/1.1 {status}\r\n",
            f"Content-Type: {mimetypes.guess_type(path)[0] or 'application/octet-stream'}\r\n",
            f"Content-Length: {count}\r\n",
            "Accept-Ranges: bytes\r\n",
        ]
        if status.startswith("206"):
            response.append(
                f"Content-Range: bytes {offset}-{offset + count - 1}/{size}\r\n"
            )
        response.append(
            "Connection: keep-alive\r\n\r\n" if keep_alive else "Connection: close\r\n\r\n"
        )
        writer.write("".join(response).encode("latin-1"))
        await writer.drain()

        if method == "GET":
            await self._send_body(writer, path, offset, count)
        return keep_alive

    def _resolve(self, target: str) -> Optional[str]:
        """URL target -> validated file path; anything else is a 404.

        Same access boundary as the link retriever: only files still inside a
        configured music folder are served, and outside is indistinguishable
        from absent.
        """
        if not target.startswith("/audio/"):
            return None
        track_id = target[len("/audio/"):]
        if not track_id or "/" in track_id or "?" in track_id:
            return None
        track = self._db.get_track_by_id(track_id)
        path = (track or {}).get("file_path")
        if (
            not path
            or not path_within_roots(path, self._roots)
            or not os.path.isfile(path)
        ):
            return None
        return path

    async def _send_body(self, writer, path: str, offset: int, count: int) -> None:
        loop = asyncio.get_running_loop()
        with open(path, "rb") as f:
            try:
                await writer.drain()
                await loop.sendfile(writer.transport, f, offset, count)
            except (NotImplementedError, AttributeError):
                f.seek(offset)
                remaining = count
                while remaining > 0:
                    chunk = f.read(min(_SENDFILE_FALLBACK_CHUNK, remaining))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    remaining -= len(chunk)

    async def _send_error(
        self, writer, status: str, keep_alive: bool = False, extra: str = ""
    ) -> None:
        connection = "keep-alive" if keep_alive else "close"
        writer.write(
            (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Length: 0\r\n"
                f"{extra}"
                f"Connection: {connection}\r\n\r\n"
            ).encode("latin-1")
        )
        await writer.drain()

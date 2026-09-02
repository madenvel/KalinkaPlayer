"""What a renderer connection can be asked to send.

The connection object itself lives in :mod:`renderer_ws_handler`, which owns
the socket. Everything else — the registry that holds one per renderer, the
session pool that drives playback over it, the settings service that queries it
— only sends on it, and says so by depending on this protocol rather than on
the handler (which depends on them).

Only the operations those three need are here. The handshake (Welcome) and the
teardown (Goodbye) belong to the connection's own lifecycle and stay on the
concrete class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .renderer_proto import renderer_pb2 as pb

if TYPE_CHECKING:  # avoids a cycle: sessions depend on this module
    from .renderer_sessions import CloseReason


class RendererLink(Protocol):
    """The send side of one renderer connection."""

    def next_message_id(self) -> int:
        """Reserve the id a reply will echo in ``in_reply_to``."""
        ...

    async def send_session_open(
        self,
        session_id: str,
        force_fixed_output: bool = False,
    ) -> None: ...

    async def send_command(self, session_id: str, command: pb.Command) -> None: ...

    async def send_session_close(
        self, session_id: str, reason: "CloseReason"
    ) -> None: ...

    async def send_config_request(self, message_id: int) -> None: ...

    async def send_config_update(
        self, message_id: int, changes: dict[str, str]
    ) -> None: ...

    async def send_upgrade(self, message_id: int, target_version: str) -> None:
        """Ask the renderer to install ``target_version`` and restart into it.

        Carried by every protocol version, so it reaches a renderer this Core
        can no longer drive — which is the one that needs it."""
        ...

    async def replace(self) -> None:
        """Retire this connection in favour of a newer one for the same
        renderer."""
        ...

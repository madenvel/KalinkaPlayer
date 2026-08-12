import asyncio
import hashlib
import logging
from dataclasses import dataclass

from .config_model import KalinkaConfig
from .renderer_ws_handler import PROTOCOL_VERSION as RENDERER_PROTOCOL_VERSION
from .server_identity import get_server_id
from .version import get_version, get_rest_api_version

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

import socket
import netifaces


logger = logging.getLogger(__name__.split(".")[-1])

_SERVICE_TYPE = "_kalinkaplayer._tcp.local."
_MAX_INSTANCE_BYTES = 63
# One TXT character-string is at most 255 bytes. ``display_name=`` consumes 13.
_MAX_DISPLAY_NAME_BYTES = 242


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Return a bounded UTF-8 prefix without splitting a codepoint."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _instance_label(display_name: str, endpoint_name: str) -> str:
    """Build a readable, RFC-compliant DNS-SD instance label."""
    # The configured display name is retained separately in TXT. Control
    # characters are illegal in a DNS-SD instance, so keep them out of this
    # transport-level identifier without rewriting what the user sees.
    safe_display = "".join(
        " " if ord(char) < 0x20 or ord(char) == 0x7F else char
        for char in display_name
    )
    suffix = f" ({endpoint_name})"
    if len(suffix.encode("utf-8")) > _MAX_INSTANCE_BYTES:
        digest = hashlib.sha256(endpoint_name.encode("utf-8")).hexdigest()[:12]
        suffix = f" ({digest})"
    prefix_bytes = _MAX_INSTANCE_BYTES - len(suffix.encode("utf-8"))
    return _truncate_utf8(safe_display, prefix_bytes) + suffix


def _endpoint_name(interface_name: str, ip_address: str, address_count: int) -> str:
    """Distinguish addresses only when one interface has more than one."""
    if address_count == 1:
        return interface_name
    return f"{interface_name}-{socket.inet_aton(ip_address).hex()}"


def get_interface_ip_mappings() -> dict[str, list[str]]:
    """
    Get a mapping of network interface names to their IP addresses.
    Excludes loopback interfaces.
    Returns:
        Dictionary mapping interface names to all their IPv4 addresses.
    """
    interface_ips: dict[str, list[str]] = {}

    for interface in netifaces.interfaces():
        try:
            # Skip loopback interface
            if interface == "lo":
                continue

            addrs = netifaces.ifaddresses(interface)
            addresses = []
            for addr_info in addrs.get(netifaces.AF_INET, []):
                ip = addr_info.get("addr")
                if ip and not ip.startswith("127.") and ip not in addresses:
                    addresses.append(ip)
            if addresses:
                interface_ips[interface] = addresses
        except (KeyError, ValueError):
            # Skip interfaces that don't have proper addressing
            continue

    return interface_ips


def get_service_info(
    config: KalinkaConfig, endpoint_name: str, ip_address: str
) -> ServiceInfo:
    """
    Create the ServiceInfo announced on one interface.

    One instance per address, carrying only that address, so a
    client never learns an address from a network it cannot reach. The
    instance name has a DNS-safe endpoint suffix — independent per-address
    responders sharing a name would trip RFC 6762 conflict defence. Clients
    group the instances of one Core by the server_id TXT value and show the
    display_name TXT value, so the suffix stays invisible.
    """

    instance_label = _instance_label(config.server.service_name, endpoint_name)
    desc = {
        "kalinka_api_version": get_rest_api_version(),
        "server_version": get_version(),
        # Renderer capability: presence = /renderer/ws exists, value = protocol
        # version. Renderers skip servers without a compatible value.
        "renderer_proto": str(RENDERER_PROTOCOL_VERSION),
        "server_id": get_server_id(),
        "display_name": _truncate_utf8(
            config.server.service_name, _MAX_DISPLAY_NAME_BYTES
        ),
    }

    return ServiceInfo(
        type_=_SERVICE_TYPE,
        name=f"{instance_label}.{_SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip_address)],
        port=config.server.port,
        properties=desc,
    )


@dataclass
class _Announcement:
    interface: str
    ip_address: str
    service_info: ServiceInfo
    zeroconf: AsyncZeroconf | None = None


class ServiceDiscovery:
    def __init__(
        self, config: KalinkaConfig, *, bind_host: str | None = None
    ):
        self.announcements: list[_Announcement] = []

        interface_ips = get_interface_ip_mappings()
        if not interface_ips:
            logger.warning("[Zeroconf] No network interfaces found, skip")
            return

        configured_interface = config.server.interface
        if bind_host is not None and bind_host != "0.0.0.0":
            candidates = (
                interface_ips
                if configured_interface == "all"
                else (configured_interface,)
            )
            bound_interface = next(
                (
                    name
                    for name in candidates
                    if bind_host in interface_ips.get(name, [])
                ),
                None,
            )
            if bound_interface is None:
                logger.warning(
                    f"[Zeroconf] Uvicorn bind address '{bind_host}' is not "
                    "assigned to a current interface, skip"
                )
                return
            # This is the authoritative listener selected by __main__. Avoid
            # relying on netifaces address ordering when an interface owns
            # several addresses.
            interface_ips = {bound_interface: [bind_host]}
            configured_interface = bound_interface

        if (
            configured_interface != "all"
            and configured_interface not in interface_ips
        ):
            logger.warning(
                f"[Zeroconf] Configured interface '{configured_interface}' not found, falling back to all interfaces"
            )
            configured_interface = "all"

        if configured_interface != "all":
            # Without an explicit bind_host (for callers outside __main__),
            # mirror get_ip_address()'s primary-address convention. With
            # "all", Uvicorn binds 0.0.0.0 and every address below is valid.
            interface_ips = {
                configured_interface: [
                    interface_ips[configured_interface][0]
                ]
            }

        endpoints = [
            (name, ip, _endpoint_name(name, ip, len(addresses)))
            for name, addresses in interface_ips.items()
            for ip in addresses
        ]
        logger.info(
            f"[Zeroconf] Announcing {len(endpoints)} instance(s) on "
            f"{len(interface_ips)} interface(s): "
            + ", ".join(f"{name} ({ip})" for name, ip, _ in endpoints)
        )
        self.announcements = [
            _Announcement(name, ip, get_service_info(config, endpoint_name, ip))
            for name, ip, endpoint_name in endpoints
        ]

    async def register_service(self):
        """Register one service instance per announced address."""
        await asyncio.gather(
            *(self._register_announcement(a) for a in self.announcements)
        )

    async def unregister_service(self):
        """Unregister every service instance, waiting out the goodbyes."""
        await asyncio.gather(
            *(self._close_announcement(a) for a in self.announcements)
        )

    async def _register_announcement(self, announcement: _Announcement):
        try:
            announcement.zeroconf = AsyncZeroconf(
                ip_version=IPVersion.V4Only,
                interfaces=[announcement.ip_address],
            )
            await announcement.zeroconf.async_register_service(
                announcement.service_info
            )
            logger.info(
                f"[Zeroconf] Registered service: "
                f"{announcement.service_info.name} on "
                f"{announcement.interface} ({announcement.ip_address})"
            )
        except asyncio.CancelledError:
            await self._close_announcement(announcement)
            raise
        except Exception as e:
            logger.error(
                f"[Zeroconf] Failed to register service "
                f"{announcement.service_info.name}: {e}"
            )
            await self._close_announcement(announcement, log=False)

    async def _close_announcement(
        self, announcement: _Announcement, *, log: bool = True
    ):
        zeroconf = announcement.zeroconf
        if zeroconf is None:
            return

        try:
            if log:
                logger.info(
                    f"[Zeroconf] Unregistering service: "
                    f"{announcement.service_info.name}"
                )
            # async_close() unregisters the service and awaits the goodbye
            # broadcasts; async_unregister_service() only schedules them.
            await zeroconf.async_close()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if log:
                logger.error(
                    f"[Zeroconf] Failed to unregister service "
                    f"{announcement.service_info.name}: {e}"
                )
        finally:
            announcement.zeroconf = None

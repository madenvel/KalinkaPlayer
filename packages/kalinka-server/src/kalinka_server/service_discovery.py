import contextlib
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


def get_interface_ip_mappings() -> dict[str, str]:
    """
    Get a mapping of network interface names to their IP addresses.
    Excludes loopback interfaces.
    Returns:
        Dictionary mapping interface names to their IP addresses.
    """
    interface_ips = {}

    for interface in netifaces.interfaces():
        try:
            # Skip loopback interface
            if interface == "lo":
                continue

            addrs = netifaces.ifaddresses(interface)
            # Check if interface has IPv4 addresses
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    ip = addr_info.get("addr")
                    if ip and not ip.startswith("127."):
                        interface_ips[interface] = ip
                        break  # Use the first valid IP for this interface
        except (KeyError, ValueError):
            # Skip interfaces that don't have proper addressing
            continue

    return interface_ips


def get_service_info(
    config: KalinkaConfig, interface_name: str, ip_address: str
) -> ServiceInfo:
    """
    Create the ServiceInfo announced on one interface.

    One instance per interface, carrying only that interface's address, so a
    client never learns an address from a network it cannot reach. The
    instance name is suffixed with the interface — independent per-interface
    responders sharing a name would trip RFC 6762 conflict defence. Clients
    group the instances of one Core by the server_id TXT value and show
    display_name, so the suffix stays invisible.
    """

    desc = {
        "kalinka_api_version": get_rest_api_version(),
        "server_version": get_version(),
        # Renderer capability: presence = /renderer/ws exists, value = protocol
        # version. Renderers skip servers without a compatible value.
        "renderer_proto": str(RENDERER_PROTOCOL_VERSION),
        "server_id": get_server_id(),
        "display_name": config.server.service_name,
    }

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{config.server.service_name} ({interface_name})"
        "._kalinkaplayer._tcp.local.",
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
    def __init__(self, config: KalinkaConfig):
        self.announcements: list[_Announcement] = []

        interface_ips = get_interface_ip_mappings()
        if not interface_ips:
            logger.warning("[Zeroconf] No network interfaces found, skip")
            return

        configured_interface = config.server.interface
        if (
            configured_interface != "all"
            and configured_interface not in interface_ips
        ):
            logger.warning(
                f"[Zeroconf] Configured interface '{configured_interface}' not found, falling back to all interfaces"
            )
            configured_interface = "all"

        if configured_interface != "all":
            interface_ips = {
                configured_interface: interface_ips[configured_interface]
            }

        logger.info(
            f"[Zeroconf] Announcing on {len(interface_ips)} interface(s): "
            + ", ".join(f"{name} ({ip})" for name, ip in interface_ips.items())
        )
        self.announcements = [
            _Announcement(name, ip, get_service_info(config, name, ip))
            for name, ip in interface_ips.items()
        ]

    async def register_service(self):
        """Register one service instance per announced interface."""
        for announcement in self.announcements:
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
            except Exception as e:
                logger.error(
                    f"[Zeroconf] Failed to register service "
                    f"{announcement.service_info.name}: {e}"
                )
                if announcement.zeroconf is not None:
                    with contextlib.suppress(Exception):
                        await announcement.zeroconf.async_close()
                    announcement.zeroconf = None

    async def unregister_service(self):
        """Unregister every service instance, waiting out the goodbyes."""
        for announcement in self.announcements:
            if announcement.zeroconf is None:
                continue

            try:
                logger.info(
                    f"[Zeroconf] Unregistering service: "
                    f"{announcement.service_info.name}"
                )
                # async_close() unregisters the service and awaits the goodbye
                # broadcasts; async_unregister_service() only schedules them.
                await announcement.zeroconf.async_close()
            except Exception as e:
                logger.error(
                    f"[Zeroconf] Failed to unregister service "
                    f"{announcement.service_info.name}: {e}"
                )
            finally:
                announcement.zeroconf = None

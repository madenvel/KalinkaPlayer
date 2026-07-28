import logging
from .config_model import KalinkaConfig
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


def get_service_info(config: KalinkaConfig, ip_addresses: list[str]) -> ServiceInfo:
    """
    Create the single ServiceInfo announced for this server.

    One instance, named exactly after the configured service name, carrying an
    A record per address. A single responder announcing the same record set on
    every interface cannot conflict with itself, so no per-interface suffix is
    needed (RFC 6762 name defense only applies between independent responders).
    """

    desc = {
        "kalinka_api_version": get_rest_api_version(),
        "server_version": get_version(),
    }

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{config.server.service_name}._kalinkaplayer._tcp.local.",
        addresses=[socket.inet_aton(ip) for ip in ip_addresses],
        port=config.server.port,
        properties=desc,
    )


class ServiceDiscovery:
    def __init__(self, config: KalinkaConfig):
        self.config = config
        self.zeroconf: AsyncZeroconf | None = None
        self.service_info: ServiceInfo | None = None
        self.ip_addresses: list[str] = []

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

        if configured_interface == "all":
            # Deduplicate while keeping interface order
            self.ip_addresses = list(dict.fromkeys(interface_ips.values()))
            logger.info(
                f"[Zeroconf] Announcing on {len(interface_ips)} interfaces: "
                + ", ".join(f"{name} ({ip})" for name, ip in interface_ips.items())
            )
        else:
            self.ip_addresses = [interface_ips[configured_interface]]
            logger.info(
                f"[Zeroconf] Announcing on interface {configured_interface} "
                f"({self.ip_addresses[0]})"
            )

        self.service_info = get_service_info(config, self.ip_addresses)

    async def register_service(self):
        """Register the service instance."""
        if self.service_info is None:
            return

        try:
            self.zeroconf = AsyncZeroconf(
                ip_version=IPVersion.V4Only, interfaces=self.ip_addresses
            )
            await self.zeroconf.async_register_service(self.service_info)
            logger.info(
                f"[Zeroconf] Registered service: {self.service_info.name} on "
                f"{[socket.inet_ntoa(addr) for addr in self.service_info.addresses]}"
            )
        except Exception as e:
            logger.error(
                f"[Zeroconf] Failed to register service {self.service_info.name}: {e}"
            )

    async def unregister_service(self):
        """Unregister the service instance."""
        if self.zeroconf is None or self.service_info is None:
            return

        try:
            logger.info(f"[Zeroconf] Unregistering service: {self.service_info.name}")
            await self.zeroconf.async_unregister_service(self.service_info)
            await self.zeroconf.async_close()
        except Exception as e:
            logger.error(
                f"[Zeroconf] Failed to unregister service {self.service_info.name}: {e}"
            )
        finally:
            self.zeroconf = None

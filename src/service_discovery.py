import logging
from src.config_model import KalinkaConfig
from src.version import get_version, get_api_version

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
    config: KalinkaConfig,
    interface_name: str,
    ip_address: str,
) -> ServiceInfo:
    """
    Create ServiceInfo for a specific interface or for all interfaces.

    Args:
        config: The Kalinka configuration
        interface_name: Specific interface name (optional, used for logging)
        ip_address: Specific IP address to use. If None, uses config interface logic

    Returns:
        ServiceInfo object configured for the specified interface/IP
    """

    # Get dynamic version and API version
    desc = {"kalinka_api_version": get_api_version(), "server_version": get_version()}

    if ip_address is not None:
        # Use the provided IP address (for multi-interface setup)
        logger.info(
            f"[Zeroconf] Creating service info for interface {interface_name}: {ip_address}"
        )
        addresses = [socket.inet_aton(ip_address)]

    service_name = f"{config.server.service_name} ({interface_name})"

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{service_name}._kalinkaplayer._tcp.local.",
        addresses=addresses,
        port=config.server.port,
        properties=desc,
    )


class ServiceDiscovery:
    def __init__(self, config: KalinkaConfig):
        self.config = config
        self.services = []

        self.interface_ips = get_interface_ip_mappings()
        configured_interface = config.server.interface

        if not self.interface_ips:
            logger.warning("[Zeroconf] No network interfaces found, skip")
            return

        # Determine which services to create
        if (
            configured_interface == "all"
            or configured_interface not in self.interface_ips
        ):
            # Create separate service instances for each interface

            if (
                configured_interface not in self.interface_ips
                and configured_interface != "all"
            ):
                logger.warning(
                    f"[Zeroconf] Configured interface '{configured_interface}' not found, falling back to all interfaces"
                )

            logger.info(
                f"[Zeroconf] Creating service instances for {len(self.interface_ips)} interfaces"
            )
            for interface_name, ip_address in self.interface_ips.items():
                service_info = get_service_info(config, interface_name, ip_address)
                self.services.append((None, service_info, interface_name))
        else:
            # Single interface case
            self.services.append(
                (
                    None,
                    get_service_info(
                        config,
                        configured_interface,
                        self.interface_ips[configured_interface],
                    ),
                    configured_interface,
                )
            )

    async def register_service(self):
        """Register all service instances."""
        logger.info(
            f"[Zeroconf] Registering {len(self.services)} service instance(s)..."
        )

        for i, (_, service_info, interface_name) in enumerate(self.services):
            try:
                ip_address = self.interface_ips[interface_name]
                # Convert IP address to interface specification for zeroconf
                interfaces = [ip_address]
                logger.info(
                    f"[Zeroconf] Binding service to interface {interface_name} ({ip_address})"
                )
                zci = AsyncZeroconf(ip_version=IPVersion.V4Only, interfaces=interfaces)

                await zci.async_register_service(service_info)
                # Update the zeroconf instance in our list
                self.services[i] = (zci, service_info, interface_name)
                logger.info(
                    f"[Zeroconf] Registered service: {service_info.name} on {[socket.inet_ntoa(addr) for addr in service_info.addresses]}"
                )
            except Exception as e:
                logger.error(
                    f"[Zeroconf] Failed to register service {service_info.name}: {e}"
                )

        logger.info("[Zeroconf] Completed registering all services.")

    async def unregister_service(self):
        """Unregister all service instances."""
        logger.info("[Zeroconf] Unregistering all services...")

        for zci, service_info, interface_name in self.services:
            if zci is not None:
                try:
                    logger.info(
                        f"[Zeroconf] Unregistering service: {service_info.name} from interface {interface_name}"
                    )
                    await zci.async_unregister_service(service_info)
                    await zci.async_close()
                except Exception as e:
                    logger.error(
                        f"[Zeroconf] Failed to unregister service {service_info.name}: {e}"
                    )

        logger.info("[Zeroconf] Completed unregistering all services.")

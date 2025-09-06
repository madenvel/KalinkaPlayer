import logging
from src.config_model import KalinkaConfig
from src.netutils import get_ip_address, get_all_ip_addresses
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
    interface_name: str | None = None,
    ip_address: str | None = None,
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
    server_cfg = config.server

    # Get dynamic version and API version
    desc = {"kalinka_api_version": get_api_version(), "server_version": get_version()}

    if ip_address is not None:
        # Use the provided IP address (for multi-interface setup)
        logger.info(
            f"[Zeroconf] Creating service info for interface {interface_name}: {ip_address}"
        )
        addresses = [socket.inet_aton(ip_address)]
    elif server_cfg.interface == "all":
        # Legacy behavior: single service with multiple IP addresses
        ip_addresses = get_all_ip_addresses()
        # If no IP addresses found, fallback to getting IP of default interface
        if not ip_addresses:
            # Try to get default route interface IP
            try:
                # Create a socket to determine the default route
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                default_ip = s.getsockname()[0]
                s.close()
                ip_addresses = [default_ip]
            except Exception:
                # Ultimate fallback
                ip_addresses = ["127.0.0.1"]

        # Log the IP addresses being advertised and their order
        logger.info(f"[Zeroconf] Advertising service on IP addresses: {ip_addresses}")
        logger.info(f"[Zeroconf] Primary (first) IP address: {ip_addresses[0]}")

        addresses = [socket.inet_aton(ip) for ip in ip_addresses]
    else:
        # Single interface case
        ip_addr = get_ip_address(server_cfg.interface)
        logger.info(
            f"[Zeroconf] Advertising service on interface {server_cfg.interface}: {ip_addr}"
        )
        addresses = [socket.inet_aton(ip_addr)]

    # Create unique service name for each interface when running multiple instances
    service_name = server_cfg.service_name
    if interface_name and ip_address:
        service_name = f"{server_cfg.service_name}_{interface_name}"

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{service_name}._kalinkaplayer._tcp.local.",
        addresses=addresses,
        port=server_cfg.port,
        properties=desc,
    )


class ServiceDiscovery:
    def __init__(self, config: KalinkaConfig):
        self.config = config
        self.services = []  # List of (zeroconf_instance, service_info) tuples

        # Determine which services to create
        if config.server.interface == "all":
            # Create separate service instances for each interface
            interface_ips = get_interface_ip_mappings()

            if not interface_ips:
                logger.warning(
                    "[Zeroconf] No network interfaces found, falling back to single service"
                )
                # Fallback to single service
                self.services.append((None, get_service_info(config)))
            else:
                logger.info(
                    f"[Zeroconf] Creating service instances for {len(interface_ips)} interfaces"
                )
                for interface_name, ip_address in interface_ips.items():
                    service_info = get_service_info(config, interface_name, ip_address)
                    self.services.append((None, service_info))
        else:
            # Single interface case
            self.services.append((None, get_service_info(config)))

    async def register_service(self):
        """Register all service instances."""
        logger.info(
            f"[Zeroconf] Registering {len(self.services)} service instance(s)..."
        )

        for i, (_, service_info) in enumerate(self.services):
            try:
                zci = AsyncZeroconf(ip_version=IPVersion.V4Only)
                await zci.async_register_service(service_info)
                # Update the zeroconf instance in our list
                self.services[i] = (zci, service_info)
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

        for zci, service_info in self.services:
            if zci is not None:
                try:
                    logger.info(
                        f"[Zeroconf] Unregistering service: {service_info.name}"
                    )
                    await zci.async_unregister_service(service_info)
                    await zci.async_close()
                except Exception as e:
                    logger.error(
                        f"[Zeroconf] Failed to unregister service {service_info.name}: {e}"
                    )

        logger.info("[Zeroconf] Completed unregistering all services.")

import logging
from src.config_model import KalinkaConfig
from src.netutils import get_ip_address, get_all_ip_addresses
from src.version import get_version, get_api_version

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

import socket


logger = logging.getLogger(__name__.split(".")[-1])


def get_service_info(config: KalinkaConfig) -> ServiceInfo:
    server_cfg = config.server

    # Get dynamic version and API version
    desc = {
        "kalinka_api_version": get_api_version(),
        "server_version": get_version()
    }

    # Handle "all" interface case by getting all available IP addresses
    if server_cfg.interface == "all":
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
        addresses = [socket.inet_aton(ip) for ip in ip_addresses]
    else:
        # Single interface case
        ip_address = get_ip_address(server_cfg.interface)
        addresses = [socket.inet_aton(ip_address)]

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{server_cfg.service_name}._kalinkaplayer._tcp.local.",
        addresses=addresses,
        port=server_cfg.port,
        properties=desc,
    )


class ServiceDiscovery:
    def __init__(self, config: KalinkaConfig):
        self.info = get_service_info(config)

    async def register_service(self):
        self.zci = AsyncZeroconf(ip_version=IPVersion.V4Only)
        logger.info("[Zeroconf] Registering service...")
        await self.zci.async_register_service(self.info)
        logger.info("[Zeroconf] Completed registering service.")

    async def unregister_service(self):
        logger.info("[Zeroconf] Unregistering service...")
        await self.zci.async_unregister_service(self.info)
        await self.zci.async_close()
        logger.info("[Zeroconf] Completed unregistering service.")

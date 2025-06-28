import logging
from src.config_model import KalinkaConfig
from src.netutils import get_ip_address

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

import socket


logger = logging.getLogger(__name__.split(".")[-1])

desc = {"kalinka_api_version": "0.1", "server_version": "1.4.0"}


def get_service_info(config: KalinkaConfig) -> ServiceInfo:
    server_cfg = config.server

    return ServiceInfo(
        type_="_kalinkaplayer._tcp.local.",
        name=f"{server_cfg.service_name}._kalinkaplayer._tcp.local.",
        addresses=[socket.inet_aton(get_ip_address(server_cfg.interface))],
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

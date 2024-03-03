import logging
from src.netutils import get_ip_address
from src.config import config

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf


logger = logging.getLogger(__name__)

desc = {
    "rpiplayer_api_version": "0.1",
}


def get_service_info():
    server_cfg = config["server"]

    return ServiceInfo(
        type_="_rpiplayer._tcp.local.",
        name=f"{server_cfg['service_name']}._rpiplayer._tcp.local.",
        addresses=[get_ip_address(server_cfg["interface"])],
        port=server_cfg["port"],
        properties=desc,
    )


class ServiceDiscovery:
    async def register_service(self):
        self.zci = AsyncZeroconf(ip_version=IPVersion.V4Only)
        logger.info("[Zeroconf] Registering service...")
        info = get_service_info()
        await self.zci.async_register_service(info)
        logger.info("[Zeroconf] Completed registering service.")

    async def unregister_service(self):
        logger.info("[Zeroconf] Unregistering service...")
        info = get_service_info()
        await self.zci.async_unregister_service(info)
        await self.zci.async_close()
        logger.info("[Zeroconf] Completed unregistering service.")

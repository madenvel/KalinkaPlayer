"""
Internal modules manager for Kalinka server.

This module manages internal server features that are separate from the plugin ecosystem.
These are built-in features that provide core functionality.
"""

import logging
from typing import Optional

from .device_automation import DeviceAutomation
from .config_model import KalinkaConfig
from .output_device_router import OutputDeviceRouter
from .player_setup import PlayerContext

logger = logging.getLogger(__name__.split(".")[-1])


class InternalModules:
    """Manager for internal server modules."""

    def __init__(self):
        self.device_automation: Optional[DeviceAutomation] = None

    async def initialize(
        self,
        config: KalinkaConfig,
        player_context: PlayerContext,
        router: OutputDeviceRouter,
    ):
        """
        Initialize all internal modules.

        Args:
            config: Server configuration
            player_context: Player context with event buses and playqueue
            router: Resolves which module owns the active renderer's output,
                so automation powers the one actually in use.
        """
        logger.info("Initializing internal modules")

        # Initialize device automation
        await self._setup_device_automation(config, player_context, router)

        logger.info("Internal modules initialization complete")

    async def _setup_device_automation(
        self,
        config: KalinkaConfig,
        player_context: PlayerContext,
        router: OutputDeviceRouter,
    ):
        """Setup the device automation module."""
        self.device_automation = DeviceAutomation(
            config=config.device_automation,
            playqueue=player_context.playqueue,
            playqueue_eventbus=player_context.playqueue_eventbus,
            ext_device_eventbus=player_context.ext_device_eventbus,
            router=router,
        )
        await self.device_automation.start()
        logger.info("Device automation initialized")

    async def shutdown(self):
        """Shutdown all internal modules."""
        logger.info("Shutting down internal modules")

        # Shutdown device automation
        if self.device_automation:
            try:
                await self.device_automation.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down device automation: {e}")

        logger.info("Internal modules shutdown complete")


# Global instance
internal_modules = InternalModules()

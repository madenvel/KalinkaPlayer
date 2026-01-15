"""
Internal modules manager for Kalinka server.

This module manages internal server features that are separate from the plugin ecosystem.
These are built-in features that provide core functionality.
"""

import logging
from typing import Optional

from kalinka_plugin_sdk.ext_device import ExternalOutputDevice

from .device_automation import DeviceAutomation
from .config_model import KalinkaConfig
from .player_setup import PlayerContext, modules

logger = logging.getLogger(__name__.split(".")[-1])


class InternalModules:
    """Manager for internal server modules."""

    def __init__(self):
        self.device_automation: Optional[DeviceAutomation] = None

    async def initialize(self, config: KalinkaConfig, player_context: PlayerContext):
        """
        Initialize all internal modules.

        Args:
            config: Server configuration
            player_context: Player context with event buses and playqueue
        """
        logger.info("Initializing internal modules")

        # Initialize device automation
        await self._setup_device_automation(config, player_context)

        logger.info("Internal modules initialization complete")

    async def _setup_device_automation(
        self, config: KalinkaConfig, player_context: PlayerContext
    ):
        """Setup the device automation module."""
        # Find the first available external output device
        device = None
        if modules.enabled_devices:
            # Get the first enabled device
            device_name = next(iter(modules.enabled_devices))
            prepared_device = modules.prepared_devices[device_name]
            # Only use it if it's actually an ExternalOutputDevice
            if isinstance(prepared_device.interface, ExternalOutputDevice):
                device = prepared_device.interface
                logger.info(f"Device automation will use device: {device_name}")
            else:
                logger.warning(f"Device {device_name} is not an ExternalOutputDevice")

        if device is None:
            logger.info(
                "No external devices found, device automation will run without device control"
            )

        # Initialize device automation
        self.device_automation = DeviceAutomation(
            config=config.device_automation,
            playqueue=player_context.playqueue,
            playqueue_eventbus=player_context.playqueue_eventbus,
            ext_device_eventbus=player_context.ext_device_eventbus,
            device=device,
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

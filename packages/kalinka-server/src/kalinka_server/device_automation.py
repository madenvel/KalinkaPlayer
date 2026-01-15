"""
Device automation module for Kalinka player.

This module provides automatic device management features such as:
- Automatically turning on the device when playback starts
- Automatically turning off the device when playback stops
- Stopping playback if paused for too long
"""

import asyncio
import logging
from typing import Optional

from kalinka_eventbus import EventBus
from kalinka_plugin_sdk.api import PlayQueueController
from kalinka_plugin_sdk.datamodel import PlayerStateEnum, PlaybackState
from kalinka_plugin_sdk.events import (
    PlayQueueEvent,
    PlayQueueEventType,
    PlayQueueState,
    PlaybackStateChangedEvent,
)
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEvent,
    ExtDeviceEventType,
    ExtDeviceState,
)

from .config_model import DeviceAutomationConfig

logger = logging.getLogger(__name__.split(".")[-1])


class DeviceAutomation:
    """Handles automatic device control based on playback state changes."""

    def __init__(
        self,
        config: DeviceAutomationConfig,
        playqueue: PlayQueueController,
        playqueue_eventbus: EventBus[
            PlayQueueState, PlayQueueEventType, PlayQueueEvent
        ],
        ext_device_eventbus: EventBus[
            ExtDeviceState, ExtDeviceEventType, ExtDeviceEvent
        ],
        device: Optional[ExternalOutputDevice] = None,
    ):
        """
        Initialize the device automation module.

        Args:
            config: Configuration for device automation features
            playqueue: The playqueue controller to control playback
            playqueue_eventbus: Event bus for playback events
            ext_device_eventbus: Event bus for device events
            device: Optional external output device to control
        """
        self.config = config
        self.playqueue = playqueue
        self.playqueue_eventbus = playqueue_eventbus
        self.ext_device_eventbus = ext_device_eventbus
        self.device = device

        self._last_state: Optional[PlayerStateEnum] = None
        self._pause_task: Optional[asyncio.Task] = None
        self._subscription = None
        self._stream_task: Optional[asyncio.Task] = None

        # Check device capabilities
        self._can_power_on = False
        self._can_power_off = False
        self._can_check_power = False

        if self.device:
            supported = self.device.supported_functions()
            self._can_power_on = SupportedFunction.POWER_ON in supported
            self._can_power_off = SupportedFunction.POWER_OFF in supported
            self._can_check_power = SupportedFunction.IS_POWER_ON in supported

            logger.info(
                f"Device automation capabilities: power_on={self._can_power_on}, "
                f"power_off={self._can_power_off}, check_power={self._can_check_power}"
            )

    async def start(self):
        """Start the device automation module."""
        logger.info("Starting device automation module")
        logger.info(
            f"Configuration: auto_power_on={self.config.auto_power_on}, "
            f"auto_power_off={self.config.auto_power_off}, "
            f"pause_timeout_seconds={self.config.pause_timeout_seconds}"
        )

        # Start listening to playback events
        self._stream_task = asyncio.create_task(self._event_listener())

    async def _event_listener(self):
        """Listen for playback state changes."""
        try:
            async with self.playqueue_eventbus.stream(
                [PlayQueueEventType.PlaybackStateChanged]
            ) as stream:
                async for event in stream:
                    if isinstance(event, PlaybackStateChangedEvent):
                        await self._handle_playback_state_change(event.state)
        except asyncio.CancelledError:
            logger.debug("Device automation event listener cancelled")
            raise
        except Exception as e:
            logger.error(
                f"Error in device automation event listener: {e}", exc_info=True
            )

    async def _handle_playback_state_change(self, state: PlaybackState):
        """
        Handle playback state changes.

        Args:
            state: The new playback state
        """
        new_state = state.state
        logger.debug(f"Playback state changed: {self._last_state} -> {new_state}")

        # Handle auto power on when playback starts
        if new_state == PlayerStateEnum.PLAYING:
            await self._on_playing()
            self._cancel_pause_timer()

        # Handle auto power off when playback stops
        elif new_state == PlayerStateEnum.STOPPED:
            await self._on_stopped()
            self._cancel_pause_timer()

        # Handle pause timeout
        elif new_state == PlayerStateEnum.PAUSED:
            await self._on_paused()

        # Cancel pause timer on buffering or error
        elif new_state in (PlayerStateEnum.BUFFERING, PlayerStateEnum.ERROR):
            self._cancel_pause_timer()

        self._last_state = new_state

    async def _on_playing(self):
        """Handle transition to playing state."""
        if not self.config.auto_power_on:
            return

        if not self.device or not self._can_power_on:
            logger.debug("Device power on not available")
            return

        try:
            # Check if device is already on (if we can check)
            if self._can_check_power:
                is_on = await self.device.is_power_on()
                if is_on:
                    logger.debug("Device is already powered on")
                    return

            # Turn on the device
            logger.info("Auto power on: Turning on device")
            await self.device.power_on()

        except Exception as e:
            logger.error(f"Failed to auto power on device: {e}", exc_info=True)

    async def _on_stopped(self):
        """Handle transition to stopped state."""
        if not self.config.auto_power_off:
            return

        if not self.device or not self._can_power_off:
            logger.debug("Device power off not available")
            return

        try:
            # Check if device is on (if we can check)
            if self._can_check_power:
                is_on = await self.device.is_power_on()
                if not is_on:
                    logger.debug("Device is already powered off")
                    return

            # Turn off the device
            logger.info("Auto power off: Turning off device")
            await self.device.power_off()

        except Exception as e:
            logger.error(f"Failed to auto power off device: {e}", exc_info=True)

    async def _on_paused(self):
        """Handle transition to paused state."""
        if self.config.pause_timeout_seconds <= 0:
            logger.debug("Pause timeout disabled")
            return

        # Cancel any existing pause timer
        self._cancel_pause_timer()

        # Start a new pause timer
        logger.debug(
            f"Starting pause timer for {self.config.pause_timeout_seconds} seconds"
        )
        self._pause_task = asyncio.create_task(self._pause_timeout())

    async def _pause_timeout(self):
        """Wait for pause timeout and stop playback."""
        try:
            await asyncio.sleep(self.config.pause_timeout_seconds)

            # Check if we're still paused
            current_state = await self.playqueue.get_playback_state()
            if current_state.state == PlayerStateEnum.PAUSED:
                logger.info(
                    f"Pause timeout reached ({self.config.pause_timeout_seconds}s), "
                    "stopping playback"
                )
                # Stop playback - this will trigger the stopped event
                await self.playqueue.stop()

        except asyncio.CancelledError:
            logger.debug("Pause timer cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in pause timeout handler: {e}", exc_info=True)

    def _cancel_pause_timer(self):
        """Cancel the pause timeout timer if it's running."""
        if self._pause_task and not self._pause_task.done():
            logger.debug("Cancelling pause timer")
            self._pause_task.cancel()
            self._pause_task = None

    async def shutdown(self):
        """Shutdown the device automation module."""
        logger.info("Shutting down device automation module")

        # Cancel pause timer
        self._cancel_pause_timer()

        # Cancel event listener
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        logger.info("Device automation module shut down")

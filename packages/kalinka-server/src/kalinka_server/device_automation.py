"""
Device automation module for Kalinka player.

This module provides automatic device management features such as:
- Automatically turning on the device when playback starts or buffers
- Automatically turning off the device (with a grace period) when playback
  is paused or stopped, also stopping the player for energy saving
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
    DevicePowerStateChangedEvent,
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
        self._auto_off_task: Optional[asyncio.Task] = None
        self._subscription = None
        self._stream_task: Optional[asyncio.Task] = None
        self._device_stream_task: Optional[asyncio.Task] = None
        # Set when device powers off externally; suppresses the auto-off timer until
        # the device is known to be on again.
        self._device_externally_off: bool = False

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
            f"auto_off_timeout_seconds={self.config.auto_off_timeout_seconds}"
        )

        # Start listening to playback events
        self._stream_task = asyncio.create_task(self._event_listener())

        # Start listening to device state changes (e.g. input switch)
        self._device_stream_task = asyncio.create_task(self._device_event_listener())

    async def _device_event_listener(self):
        """Listen for external device state changes (e.g. input switched away)."""
        while True:
            try:
                async with self.ext_device_eventbus.stream(
                    [ExtDeviceEventType.DevicePowerStateChanged]
                ) as stream:
                    async for event in stream:
                        if isinstance(event, DevicePowerStateChangedEvent):
                            if not event.power_on:
                                await self._on_device_power_off()
                            else:
                                self._device_externally_off = False
            except asyncio.CancelledError:
                logger.debug("Device automation device event listener cancelled")
                raise
            except Exception as e:
                logger.error(
                    f"Error in device automation device event listener: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(1)

    async def _on_device_power_off(self):
        """Handle device power-off or input switch — stop playback immediately."""
        logger.info("Device powered off or input switched — stopping playback")
        self._device_externally_off = True
        self._cancel_auto_off_timer()
        try:
            current_state = await self.playqueue.get_playback_state()
            if current_state.state in (
                PlayerStateEnum.PLAYING,
                PlayerStateEnum.BUFFERING,
                PlayerStateEnum.PAUSED,
            ):
                await self.playqueue.stop()
        except Exception as e:
            logger.error(
                f"Failed to stop playback on device power-off: {e}", exc_info=True
            )

    async def _event_listener(self):
        """Listen for playback state changes."""
        while True:
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
                await asyncio.sleep(1)

    async def _handle_playback_state_change(self, state: PlaybackState):
        """
        Handle playback state changes.

        Args:
            state: The new playback state
        """
        new_state = state.state
        logger.debug(f"Playback state changed: {self._last_state} -> {new_state}")

        if new_state in (PlayerStateEnum.BUFFERING, PlayerStateEnum.PLAYING):
            self._cancel_auto_off_timer()
            await self._on_active()

        elif new_state in (PlayerStateEnum.PAUSED, PlayerStateEnum.STOPPED):
            self._start_auto_off_timer()

        self._last_state = new_state

    async def _on_active(self):
        """Handle transition to an active state (buffering or playing)."""
        self._device_externally_off = False
        if not self.config.auto_power_on:
            return

        if not self.device or not self._can_power_on:
            logger.debug("Device power on not available")
            return

        try:
            if self._can_check_power:
                is_on = await self.device.is_power_on()
                if is_on:
                    logger.debug("Device is already powered on")
                    return

            logger.info("Auto power on: Turning on device")
            await self.device.power_on()

        except Exception as e:
            logger.error(f"Failed to auto power on device: {e}", exc_info=True)

    def _start_auto_off_timer(self):
        """Start the auto-off grace period timer."""
        if not self.config.auto_power_off:
            return

        timeout = self.config.auto_off_timeout_seconds
        if timeout <= 0:
            logger.debug("Auto-off timeout disabled")
            return

        if self._device_externally_off:
            logger.debug("Skipping auto-off timer: device already externally powered off")
            return

        # Don't restart the timer if it's already running (e.g. paused → stopped)
        if self._auto_off_task and not self._auto_off_task.done():
            logger.debug("Auto-off timer already running")
            return

        logger.debug(f"Starting auto-off timer ({timeout}s)")
        self._auto_off_task = asyncio.create_task(self._auto_off_timeout())

    async def _auto_off_timeout(self):
        """Wait for the grace period then stop playback and power off."""
        try:
            await asyncio.sleep(self.config.auto_off_timeout_seconds)

            current_state = await self.playqueue.get_playback_state()
            if current_state.state not in (
                PlayerStateEnum.PAUSED,
                PlayerStateEnum.STOPPED,
            ):
                logger.debug(
                    f"Auto-off timer fired but state is {current_state.state}, skipping"
                )
                return

            logger.info(
                f"Auto-off timeout reached ({self.config.auto_off_timeout_seconds}s), "
                "stopping playback and powering off device"
            )

            # Stop playback first for energy saving (no-op if already stopped)
            if current_state.state == PlayerStateEnum.PAUSED:
                try:
                    await self.playqueue.stop()
                except Exception as e:
                    logger.error(
                        f"Failed to stop playback on auto-off: {e}", exc_info=True
                    )

            # Power off the device
            if self.config.auto_power_off and self.device and self._can_power_off:
                try:
                    if self._can_check_power:
                        is_on = await self.device.is_power_on()
                        if not is_on:
                            logger.debug("Device is already powered off")
                            return

                    logger.info("Auto power off: Turning off device")
                    await self.device.power_off()

                except Exception as e:
                    logger.error(f"Failed to auto power off device: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.debug("Auto-off timer cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in auto-off timeout handler: {e}", exc_info=True)

    def _cancel_auto_off_timer(self):
        """Cancel the auto-off timer if it's running."""
        if self._auto_off_task and not self._auto_off_task.done():
            logger.debug("Cancelling auto-off timer")
            self._auto_off_task.cancel()
            self._auto_off_task = None

    async def shutdown(self):
        """Shutdown the device automation module."""
        logger.info("Shutting down device automation module")

        self._cancel_auto_off_timer()

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._device_stream_task:
            self._device_stream_task.cancel()
            try:
                await self._device_stream_task
            except asyncio.CancelledError:
                pass

        logger.info("Device automation module shut down")

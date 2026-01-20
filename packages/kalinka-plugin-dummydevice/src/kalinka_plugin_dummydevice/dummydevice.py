import asyncio
import logging
from kalinka_plugin_sdk.api import ReplayEvent
from kalinka_plugin_sdk.ext_device_events import (
    ExtDeviceEventType,
    ExtDeviceState,
    VolumeChangedEvent,
)
from kalinka_plugin_sdk.ext_device import (
    DeviceVolume,
    ExternalOutputDevice,
    SupportedFunction,
)

logger = logging.getLogger(__name__.split(".")[-1])


class DummyDevice(ExternalOutputDevice):
    def __init__(self, event_emitter):
        self._power_on = False
        self._volume = 50
        self._max_volume = 100
        self.event_emitter = event_emitter
        self._volume_changed_event = asyncio.Event()
        self._event_sender_task = None
        self._shutdown = False
        logger.info("DummyDevice initialized")

    async def start(self):
        """Start the event sender task. Call this after initialization."""
        # Set initial device state before starting event emission
        initial_state = ExtDeviceState(
            power_on=self._power_on,
            volume=DeviceVolume(
                max_volume=self._max_volume,
                current_volume=self._volume,
                volume_gain=0,
                supported=True,
            ),
        )
        self.event_emitter.set_initial_state(initial_state)

        self._event_sender_task = asyncio.create_task(self._event_sender_async())
        return self

    async def shutdown(self):
        """Stop the event sender task and cleanup resources."""
        self._shutdown = True
        self._volume_changed_event.set()  # Wake up the task
        if self._event_sender_task:
            self._event_sender_task.cancel()
            try:
                await self._event_sender_task
            except asyncio.CancelledError:
                pass

    async def _event_sender_async(self):
        """Async task that debounces and throttles volume change events."""
        last_sent_volume = None
        last_sent_at = 0.0
        debounce_sec = 0.10
        min_interval_sec = 0.00  # set to 0.10 to cap at 10 Hz

        try:
            while not self._shutdown:
                # Wait for volume change event
                await self._volume_changed_event.wait()
                self._volume_changed_event.clear()

                logger.info("Volume change detected: %d", self._volume)

                if self._shutdown:
                    break

                # Debounce: wait for quiet period
                try:
                    while True:
                        await asyncio.wait_for(
                            self._volume_changed_event.wait(), timeout=debounce_sec
                        )
                        self._volume_changed_event.clear()
                        if self._shutdown:
                            break
                except asyncio.TimeoutError:
                    pass  # Debounce period elapsed, proceed to send event

                if self._shutdown:
                    break

                target = self._volume

                # Throttle: ensure minimum interval between sends
                if min_interval_sec > 0:
                    loop = asyncio.get_running_loop()
                    now = loop.time()
                    remaining = (last_sent_at + min_interval_sec) - now
                    if remaining > 0:
                        # During throttle wait, keep coalescing new changes
                        try:
                            await asyncio.wait_for(
                                self._volume_changed_event.wait(), timeout=remaining
                            )
                            # New change arrived; restart loop to re-debounce
                            self._volume_changed_event.clear()
                            continue
                        except asyncio.TimeoutError:
                            pass  # Throttle period elapsed

                if self._shutdown:
                    break

                # Send event if volume actually changed
                if target != last_sent_volume:
                    self.event_emitter.dispatch(
                        VolumeChangedEvent.model_construct(
                            event_type=ExtDeviceEventType.VolumeChanged,
                            volume=DeviceVolume(
                                max_volume=self._max_volume,
                                current_volume=target,
                                volume_gain=0,
                                supported=True,
                            ),
                        )
                    )
                    last_sent_volume = target
                    loop = asyncio.get_running_loop()
                    last_sent_at = loop.time()
        except asyncio.CancelledError:
            logger.debug("Event sender task cancelled")
            raise

    async def get_volume(self) -> DeviceVolume:
        return DeviceVolume(
            max_volume=self._max_volume,
            current_volume=self._volume,
            volume_gain=0,
            supported=True,
        )

    async def set_volume(self, volume: int) -> None:
        logger.debug("Setting volume to %d", volume)
        if 0 <= volume <= self._max_volume:
            self._volume = volume
            self._volume_changed_event.set()
        else:
            raise ValueError(f"Volume must be between 0 and {self._max_volume}")

    async def power_on(self) -> None:
        self._power_on = True

    async def is_power_on(self) -> bool:
        return self._power_on

    async def power_off(self) -> None:
        self._power_on = False

    def supported_functions(self) -> list[SupportedFunction]:
        return [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
            SupportedFunction.POWER_ON,
            SupportedFunction.IS_POWER_ON,
            SupportedFunction.POWER_OFF,
        ]

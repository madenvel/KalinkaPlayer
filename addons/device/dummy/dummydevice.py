import logging
import threading
import time
from src.events import EventType
from src.ext_device import DeviceVolume, ExternalOutputDevice, SupportedFunction

logger = logging.getLogger(__name__.split(".")[-1])


class DummyDevice(ExternalOutputDevice):
    def __init__(self, event_emitter):
        self._power_on = False
        self._volume = 50
        self._max_volume = 100
        self.event_emitter = event_emitter
        self.volume_changed_event = threading.Event()
        threading.Thread(
            target=self._event_sender, name="DummyVolumeEventSenderThread", daemon=True
        ).start()
        logger.info("DummyDevice initialized")

    def _event_sender(self):
        last_sent_volume = None
        last_sent_at = 0.0
        debounce_sec = 0.10
        min_interval_sec = 0.00  # set to 0.10 to cap at 10 Hz

        while True:
            self.volume_changed_event.wait()
            self.volume_changed_event.clear()

            # Debounce: wait for quiet
            while self.volume_changed_event.wait(timeout=debounce_sec):
                self.volume_changed_event.clear()

            target = self._volume

            # Throttle: ensure at least min_interval between sends
            if min_interval_sec > 0:
                now = time.monotonic()
                remaining = (last_sent_at + min_interval_sec) - now
                if remaining > 0:
                    # During throttle wait, keep coalescing new changes
                    if self.volume_changed_event.wait(timeout=remaining):
                        # new change arrived; restart loop to re-debounce
                        self.volume_changed_event.clear()
                        continue

            if target != last_sent_volume:
                self.event_emitter.dispatch(EventType.VolumeChanged, target)
                last_sent_volume = target
                last_sent_at = time.monotonic()

    def get_volume(self) -> DeviceVolume:
        return DeviceVolume(
            max_volume=self._max_volume,
            current_volume=self._volume,
            volume_gain=0,
            supported=True,
        )

    def set_volume(self, volume: int) -> None:
        if 0 <= volume <= self._max_volume:
            self._volume = volume
            self.volume_changed_event.set()
        else:
            raise ValueError(f"Volume must be between 0 and {self._max_volume}")

    def power_on(self) -> None:
        self._power_on = True

    def is_power_on(self) -> bool:
        return self._power_on

    def power_off(self) -> None:
        self._power_on = False

    def supported_functions(self) -> list[SupportedFunction]:
        return [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
            SupportedFunction.POWER_ON,
            SupportedFunction.IS_POWER_ON,
            SupportedFunction.POWER_OFF,
        ]

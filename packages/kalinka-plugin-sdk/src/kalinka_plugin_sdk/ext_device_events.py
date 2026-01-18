from enum import Enum
from typing import Any

from pydantic import BaseModel

from .api import BaseEvent, BaseState

from .datamodel import DeviceVolume


class ExtDeviceEventType(Enum):
    DevicePowerStateChanged = "device_power_state_changed"
    VolumeChanged = "volume_changed"


class ExtDeviceEvent(BaseEvent[ExtDeviceEventType]):
    """Base class for all external device events."""

    pass


class ExtDeviceState(BaseState[ExtDeviceEvent]):
    """State model for external device - uses Pydantic BaseModel for serialization."""

    power_on: bool
    volume: DeviceVolume

    def apply(self, event: ExtDeviceEvent) -> "ExtDeviceState":
        """Apply event and return a new state (immutable pattern)."""
        if self.seq >= event.seq:
            return self

        updates: dict[str, Any] = {"seq": event.seq}

        if isinstance(event, DevicePowerStateChangedEvent):
            updates["power_on"] = event.power_on
        elif isinstance(event, VolumeChangedEvent):
            updates["volume"] = event.volume
        else:
            return self

        return self.model_copy(update=updates)


class DevicePowerStateChangedEvent(ExtDeviceEvent):
    event_type: ExtDeviceEventType = ExtDeviceEventType.DevicePowerStateChanged
    power_on: bool


class VolumeChangedEvent(ExtDeviceEvent):
    event_type: ExtDeviceEventType = ExtDeviceEventType.VolumeChanged
    volume: DeviceVolume

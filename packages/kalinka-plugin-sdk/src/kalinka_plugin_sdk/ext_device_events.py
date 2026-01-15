from enum import Enum

from pydantic import BaseModel

from .api import BaseEvent

from .datamodel import DeviceVolume


class ExtDeviceEventType(Enum):
    DevicePowerStateChanged = "device_power_state_changed"
    VolumeChanged = "volume_changed"


class ExtDeviceEvent(BaseEvent[ExtDeviceEventType]):
    """Base class for all external device events."""

    pass


class ExtDeviceState(BaseModel):
    """State model for external device - uses Pydantic BaseModel for serialization."""

    power_on: bool
    volume: DeviceVolume

    def apply(self, event: ExtDeviceEvent) -> "ExtDeviceState":
        """Apply event to create a new state (immutable pattern)."""
        if isinstance(event, DevicePowerStateChangedEvent):
            return self.model_copy(update={"power_on": event.power_on})
        elif isinstance(event, VolumeChangedEvent):
            return self.model_copy(update={"volume": event.volume})
        return self


class DevicePowerStateChangedEvent(ExtDeviceEvent):
    event_type: ExtDeviceEventType = ExtDeviceEventType.DevicePowerStateChanged
    power_on: bool


class VolumeChangedEvent(ExtDeviceEvent):
    event_type: ExtDeviceEventType = ExtDeviceEventType.VolumeChanged
    volume: DeviceVolume

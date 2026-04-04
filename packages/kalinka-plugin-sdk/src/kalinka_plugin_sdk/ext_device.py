from enum import Enum
from typing import Protocol, runtime_checkable
from .datamodel import DeviceVolume


class SupportedFunction(Enum):
    """Enumeration of functions that can be supported by external output devices.

    This enum defines the possible capabilities that an external output device
    can implement. Devices should report which functions they support through
    the supported_functions() method.
    """

    GET_VOLUME = "get_volume"
    SET_VOLUME = "set_volume"
    POWER_ON = "power_on"
    IS_POWER_ON = "is_power_on"
    POWER_OFF = "power_off"


@runtime_checkable
class ExternalOutputDevice(Protocol):
    """Protocol for external output devices.

    This protocol defines the contract for external audio output devices
    that can be controlled by the Kalinka player. Implementing classes
    should provide device-specific implementations for volume control,
    power management, and capability reporting.

    Examples of external output devices include network speakers,
    amplifiers, DACs, or any other audio equipment that can be
    controlled programmatically.
    """

    async def get_volume(self) -> DeviceVolume:
        """Get the current volume level of the device.

        Returns:
            DeviceVolume: The current volume information including level
                         and any additional volume-related metadata.

        Raises:
            NotImplementedError: If the device doesn't support volume retrieval.
            ConnectionError: If unable to communicate with the device.
        """
        ...

    async def set_volume(self, volume: int) -> None:
        """Set the volume level of the device.

        Args:
            volume (int): The desired volume level. The valid range depends
                         on the specific device implementation, but typically
                         ranges from 0 (muted) to 100 (maximum volume).

        Raises:
            NotImplementedError: If the device doesn't support volume control.
            ValueError: If the volume value is outside the valid range.
            ConnectionError: If unable to communicate with the device.
        """
        ...

    async def power_on(self) -> None:
        """Turn on the external output device.

        This method should initiate the power-on sequence for the device.
        The implementation should handle any device-specific startup
        procedures required to make the device ready for audio output.

        Raises:
            NotImplementedError: If the device doesn't support power control.
            ConnectionError: If unable to communicate with the device.
            RuntimeError: If the device fails to power on.
        """
        ...

    async def is_power_on(self) -> bool:
        """Check if the device is currently powered on.

        Returns:
            bool: True if the device is powered on and ready for use,
                  False if the device is powered off or in standby mode.

        Raises:
            NotImplementedError: If the device doesn't support power status queries.
            ConnectionError: If unable to communicate with the device.
        """
        ...

    async def power_off(self) -> None:
        """Turn off the external output device.

        This method should initiate the power-off sequence for the device.
        The implementation should handle any device-specific shutdown
        procedures to safely power down the device.

        Raises:
            NotImplementedError: If the device doesn't support power control.
            ConnectionError: If unable to communicate with the device.
            RuntimeError: If the device fails to power off properly.
        """
        ...

    def supported_functions(self) -> list[SupportedFunction]:
        """Get a list of functions supported by this device.

        This method should return all the capabilities that the device
        implementation supports. The Kalinka player will use this information
        to determine which operations can be performed on the device.

        Returns:
            list[SupportedFunction]: A list of SupportedFunction enum values
                                   indicating which operations this device
                                   can perform.

        Example:
            For a device that supports volume control and power management:
            return [
                SupportedFunction.GET_VOLUME,
                SupportedFunction.SET_VOLUME,
                SupportedFunction.POWER_ON,
                SupportedFunction.POWER_OFF,
                SupportedFunction.IS_POWER_ON
            ]
        """
        ...

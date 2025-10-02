from kalinka_plugin_sdk.ext_device import ExternalOutputDevice, SupportedFunction, DeviceVolume

from .config_model import {{ cookiecutter.plugin_class_prefix }}Config


class {{ cookiecutter.plugin_class_prefix }}Device(ExternalOutputDevice):
    def __init__(self, config: {{ cookiecutter.plugin_class_prefix }}Config):
        self.config = config

    def get_volume(self) -> DeviceVolume:
        """Get current volume level"""
        raise NotImplementedError

    def set_volume(self, volume: float) -> None:
        """Set volume (0.0 to 1.0)"""
        raise NotImplementedError

    def power_on(self) -> None:
        """Power on the device"""
        raise NotImplementedError

    def is_power_on(self) -> bool:
        """Check if device is powered on"""
        raise NotImplementedError

    def power_off(self) -> None:
        """Power off the device"""
        raise NotImplementedError

    def supported_functions(self) -> list[SupportedFunction]:
        """Return list of supported functions"""
        return [
            SupportedFunction.GET_VOLUME,
            SupportedFunction.SET_VOLUME,
            SupportedFunction.POWER_ON,
            SupportedFunction.IS_POWER_ON,
            SupportedFunction.POWER_OFF,
        ]

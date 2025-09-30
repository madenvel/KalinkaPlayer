from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.datamodel import TrackInfo

from .config_model import {{ cookiecutter.plugin_class_prefix }}Config


class {{ cookiecutter.plugin_class_prefix }}Device(ExternalOutputDevice):
    def __init__(self, config: {{ cookiecutter.plugin_class_prefix }}Config):
        self.config = config

    def device_name(self) -> str:
        return "{{ cookiecutter.plugin_display_name }}"

    def play(self, track_info: TrackInfo):
        """Start playing the given track"""
        raise NotImplementedError

    def pause(self):
        """Pause playback"""
        raise NotImplementedError

    def resume(self):
        """Resume playback"""
        raise NotImplementedError

    def stop(self):
        """Stop playback"""
        raise NotImplementedError

    def set_volume(self, volume: float):
        """Set volume (0.0 to 1.0)"""
        raise NotImplementedError

    def get_volume(self) -> float:
        """Get current volume (0.0 to 1.0)"""
        raise NotImplementedError

    def seek(self, position: float):
        """Seek to position in seconds"""
        raise NotImplementedError

    def get_position(self) -> float:
        """Get current position in seconds"""
        raise NotImplementedError

    def is_playing(self) -> bool:
        """Check if currently playing"""
        raise NotImplementedError
from pydantic import Field
from src.base_config_model import ModuleConfig


class MusicCastConfig(ModuleConfig):
    name: str = Field(default="musiccast", title="MusicCast", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module Enabled")
    device_addr: str = Field(
        "127.0.0.1",
        title="MusicCast device address",
    )
    device_port: int = Field(5000, title="MusicCast device port")
    connected_input: str = Field("optical1", title="Connected input")
    auto_volume_correction: bool = Field(
        False,
        title="Adjust loudness using replaygain",
    )
    volume_step_to_db: float = Field(
        0.5,
        title="Autoloudness volume step in dB",
        ge=0.5,
        le=5,
    )

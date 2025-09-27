from pydantic import Field
from sdk.module_config import ModuleConfig


class MusicCastConfig(ModuleConfig):
    name: str = Field(default="musiccast", title="MusicCast", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module Enabled")
    device_addr: str = Field(
        "",
        title="Override MusicCast device IP address",
    )
    device_port: int = Field(default=80, title="Override MusicCast device port")
    connected_input: str = Field(default="optical1", title="Connected input")
    zone_name: str = Field(default="main", title="Zone name")
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
    discovery_timeout: int = Field(
        10,
        title="Device discovery timeout in seconds",
        ge=5,
        le=60,
    )

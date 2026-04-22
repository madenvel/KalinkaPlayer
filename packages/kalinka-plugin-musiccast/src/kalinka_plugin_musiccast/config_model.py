from typing import ClassVar
from pydantic import Field
from kalinka_plugin_sdk.module_config import ModuleConfig


class KalinkaPluginMusiccastConfig(ModuleConfig):
    __module_icon__: ClassVar[str] = "speaker_outlined"
    __preview_fields__: ClassVar[list[str]] = ["device_addr", "zone_name"]

    name: str = Field(default="musiccast", title="MusicCast", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module enabled")
    device_addr: str = Field(
        default="",
        title="Device IP address",
        json_schema_extra={"help": "Override MusicCast device IP"},
    )
    device_port: int = Field(
        default=80,
        title="Device port",
        json_schema_extra={"importance": "expert"},
    )
    connected_input: str = Field(default="optical1", title="Connected input")
    zone_name: str = Field(default="main", title="Zone name")
    auto_volume_correction: bool = Field(
        default=False,
        title="Auto volume correction",
        json_schema_extra={"help": "Adjust loudness using replaygain"},
    )
    volume_step_to_db: float = Field(
        default=0.5,
        title="Autoloudness volume step",
        ge=0.5,
        le=5,
        json_schema_extra={
            "importance": "advanced",
            "constraints": {"unit": "dB"},
        },
    )
    discovery_timeout: int = Field(
        default=10,
        title="Discovery timeout",
        ge=5,
        le=60,
        json_schema_extra={
            "importance": "advanced",
            "constraints": {"unit": "s"},
        },
    )

from pydantic import Field, ConfigDict
from kalinka_plugin_sdk.module_config import ModuleConfig
from enum import Enum


class QobuzAudioFormat(str, Enum):
    MP3 = "MP3 320kbps"
    CD = "CD 16-bit 44.1KHz"
    HIRES_96 = "Hi-Res 24-bit 96KHz"
    HIRES_192 = "Hi-Res 24-bit 192KHz"


class QobuzConfig(ModuleConfig):
    """Qobuz input module settings."""

    model_config = ConfigDict(use_enum_values=True)

    name: str = Field(default="qobuz", title="Qobuz", frozen=True, exclude=True)
    email: str = Field(default="my@email.com", title="Qobuz login")
    password_hash: str = Field(
        default="",
        title="Qobuz password",
        json_schema_extra={"password": True},
    )
    format: QobuzAudioFormat = Field(
        default=QobuzAudioFormat.HIRES_192, title="Audio quality"
    )

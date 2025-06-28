from enum import Enum
from pydantic import Field
from src.base_config_model import ModuleConfig
import yaml


class QobuzAudioFormat(str, Enum):
    MP3 = "MP3 320kbps"
    CD = "CD 16-bit 44.1KHz"
    HIRES_96 = "Hi-Res 24-bit 96KHz"
    HIRES_192 = "Hi-Res 24-bit 192KHz"


class QobuzConfig(ModuleConfig):
    """Qobuz input module settings."""

    name: str = Field(default="qobuz", title="Qobuz", frozen=True)
    email: str = Field(default="my@email.com", title="Qobuz login")
    password_hash: str = Field(
        default="mypassword",
        title="Qobuz password",
        json_schema_extra={"password": True},
    )
    format: QobuzAudioFormat = Field(
        default=QobuzAudioFormat.HIRES_192, title="Audio quality"
    )

    class Config:
        use_enum_values = True

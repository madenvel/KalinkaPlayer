"""Pydantic models for Kalinka configuration."""

from typing import Literal
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """Server configuration section."""

    interface: str = Field(
        default="all",
        title="Network interface to bind",
    )
    port: int = Field(
        default=8000,
        title="Port number to listen on",
    )
    service_name: str = Field(default="My Kalinka Service", title="Name of the service")
    log_level: Literal["debug", "info", "warning", "error"] = Field(
        default="info", title="Logging level"
    )


class AlsaConfig(BaseModel):
    """ALSA output configuration."""

    device: str = Field(default="default", title="ALSA device name")
    latency_ms: int = Field(default=160, title="Output latency in milliseconds")
    period_ms: int = Field(default=40, title="Output period in milliseconds")


class OutputConfig(BaseModel):
    """Output configuration section."""

    alsa: AlsaConfig = Field(default_factory=AlsaConfig, title="ALSA output settings")


class HttpInputConfig(BaseModel):
    """HTTP input configuration."""

    buffer_size: int = Field(default=384000, title="HTTP buffer size in bytes")
    chunk_size: int = Field(default=768000, title="HTTP chunk size in bytes")


class InputConfig(BaseModel):
    """Input configuration section."""

    http: HttpInputConfig = Field(
        default_factory=HttpInputConfig, title="HTTP input settings"
    )


class FlacDecoderConfig(BaseModel):
    """FLAC decoder configuration."""

    buffer_size: int = Field(default=1536000, title="FLAC buffer size in bytes")


class MpegDecoderConfig(BaseModel):
    """MPEG decoder configuration."""

    buffer_size: int = Field(default=176400, title="Buffer size in bytes")


class DecoderConfig(BaseModel):
    """Decoder configuration section."""

    flac: FlacDecoderConfig = Field(
        default_factory=FlacDecoderConfig, title="FLAC decoder settings"
    )
    mpeg: MpegDecoderConfig = Field(
        default_factory=MpegDecoderConfig, title="MPEG decoder settings"
    )


class FixupsConfig(BaseModel):
    """Hardware fixups configuration."""

    alsa_sleep_after_format_setup_ms: int = Field(
        default=0, title="ALSA sleep time after format setup"
    )
    alsa_reopen_device_with_new_format: bool = Field(
        default=False, title="Reopen ALSA device with new format"
    )


class KalinkaConfig(BaseModel):
    """Main Kalinka configuration model"""

    server: ServerConfig = Field(
        default_factory=ServerConfig, title="Name, interface and port of the server"
    )
    output: OutputConfig = Field(
        default_factory=OutputConfig, title="Output configuration"
    )
    input: InputConfig = Field(default_factory=InputConfig, title="Input configuration")
    decoder: DecoderConfig = Field(
        default_factory=DecoderConfig, title="Decoders configuration"
    )
    fixups: FixupsConfig = Field(
        default_factory=FixupsConfig, title="Hacks to work around hardware issues"
    )

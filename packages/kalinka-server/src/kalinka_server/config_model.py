"""Pydantic models for Kalinka configuration.

Per-field UI metadata is carried in `Field(json_schema_extra=...)` with keys
recognized by the presentation emitter:

    widget       — one of the values of presentation_schema.Widget
    help         — inline sublabel / help text shown under the label
    importance   — "simple" | "expert" (default: "expert")
    constraints  — dict with slider_min/slider_max/step/unit (merges with
                   Pydantic's own ge/le/etc.)

The default tier for unmarked fields is EXPERT — they're reachable only
through the about:config-style search. To put a field on the main
settings page (mandatory or frequently changed), tag it
``"importance": "simple"`` explicitly.

KalinkaConfig.presentation_layout() defines the General page grouping and
collapses the redundant `base_config` level so the UI shows peer sections.
"""

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .presentation_schema import SectionSpec


# Shared extras — keeps audit-tagging consistent across the file.
_SIMPLE = {"importance": "simple"}


class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"


class ServerConfig(BaseModel):
    interface: str = Field(
        default="all",
        title="Network interface",
        json_schema_extra={
            "help": 'Bind to a specific interface or "all"',
        },
    )
    port: int = Field(
        default=8000,
        title="Port",
        json_schema_extra={
            "help": "HTTP API port",
            "widget": "number_input",
            "constraints": {"ge": 1, "le": 65535},
            **_SIMPLE,
        },
    )
    service_name: str = Field(
        default="My Kalinka Service",
        title="Service name",
        json_schema_extra={
            "help": "Shown during Zeroconf discovery",
            **_SIMPLE,
        },
    )
    log_level: LogLevel = Field(
        default=LogLevel.info,
        title="Log level",
        json_schema_extra=_SIMPLE,
    )


class AlsaConfig(BaseModel):
    # Options are enumerated live from ALSA — they ship in the values
    # envelope under enum_options[path], so we leave enum_values empty
    # in the schema. The widget kind enum_dropdown tells the client to
    # render a dropdown; the presence of enum_options at request time
    # is what makes the option list dynamic. See alsa_options.py.
    device: str = Field(
        default="default",
        title="ALSA device",
        json_schema_extra={
            "help": (
                "Hardware output. Stored as `hw:CARD=…,DEV=…` so the "
                "selection survives card-index shuffling on kernel "
                "upgrade — ALSA itself resolves the card id to the "
                "current index at open time."
            ),
            "widget": "enum_dropdown",
            **_SIMPLE,
        },
    )
    latency_ms: int = Field(
        default=160,
        title="Output latency",
        json_schema_extra={
            "widget": "number_slider",
            "constraints": {"slider_min": 0, "slider_max": 500, "unit": "ms"},
        },
    )
    period_ms: int = Field(
        default=40,
        title="Period size",
        json_schema_extra={
            "widget": "number_slider",
            "constraints": {"slider_min": 0, "slider_max": 500, "unit": "ms"},
        },
    )


class OutputConfig(BaseModel):
    alsa: AlsaConfig = Field(default_factory=AlsaConfig, title="ALSA output")


class HttpInputConfig(BaseModel):
    buffer_size: int = Field(
        default=384000,
        title="Buffer size",
        json_schema_extra={
            "help": "HTTP input buffer (bytes)",
            "constraints": {"unit": "bytes"},
        },
    )
    chunk_size: int = Field(
        default=768000,
        title="Chunk size",
        json_schema_extra={
            "help": "HTTP input chunk (bytes)",
            "constraints": {"unit": "bytes"},
        },
    )


class InputConfig(BaseModel):
    http: HttpInputConfig = Field(default_factory=HttpInputConfig, title="HTTP input")


class FlacDecoderConfig(BaseModel):
    buffer_size: int = Field(
        default=1536000,
        title="FLAC buffer",
        json_schema_extra={
            "help": "FLAC decoder buffer (bytes)",
            "constraints": {"unit": "bytes"},
        },
    )


class MpegDecoderConfig(BaseModel):
    buffer_size: int = Field(
        default=176400,
        title="MPEG buffer",
        json_schema_extra={
            "help": "MPEG decoder buffer (bytes)",
            "constraints": {"unit": "bytes"},
        },
    )


class DecoderConfig(BaseModel):
    flac: FlacDecoderConfig = Field(
        default_factory=FlacDecoderConfig, title="FLAC decoder"
    )
    mpeg: MpegDecoderConfig = Field(
        default_factory=MpegDecoderConfig, title="MPEG decoder"
    )


class FixupsConfig(BaseModel):
    alsa_sleep_after_format_setup_ms: int = Field(
        default=0,
        title="Sleep after format setup",
        json_schema_extra={
            "help": (
                "Delay (ms) after ALSA format change. Increase if audio glitches "
                "when format switches."
            ),
            "constraints": {"unit": "ms"},
        },
    )
    alsa_reopen_device_with_new_format: bool = Field(
        default=False,
        title="Reopen device on format change",
        json_schema_extra={
            "help": "Full device reopen when format changes. Required by some DACs.",
        },
    )


class DeviceAutomationConfig(BaseModel):
    auto_power_on: bool = Field(
        default=True,
        title="Auto power on",
        json_schema_extra={
            "help": "Turn on device when playback starts",
            **_SIMPLE,
        },
    )
    auto_power_off: bool = Field(
        default=True,
        title="Auto power off",
        json_schema_extra={
            "help": "Turn off device when playback stops",
            **_SIMPLE,
        },
    )
    auto_off_timeout_seconds: int = Field(
        default=60,
        title="Pause timeout",
        json_schema_extra={
            "help": "Stop playback if paused for this many seconds (0 = disabled)",
            "constraints": {"unit": "s"},
            **_SIMPLE,
        },
    )


class SearchConfig(BaseModel):
    """Cross-source search tuning: the merged BEST MATCH block and the AI
    suggestion cards assembled by ``kalinka_server.ai_search``."""

    ai_suppress_full_match_score: int = Field(
        default=88,
        ge=0,
        le=100,
        title="Hide AI suggestions on a full-name match",
        json_schema_extra={
            "help": (
                "When a BEST MATCH entity name matches the whole query at or "
                "above this whole-string score (0–100), the query is treated as "
                "a name lookup and the AI suggestion cards are hidden. Higher = "
                "suppress less; 100 hides them only for an exact full-name match."
            ),
        },
    )
    best_match_min_score: int = Field(
        default=88,
        ge=0,
        le=100,
        title="BEST MATCH minimum score",
        json_schema_extra={
            "help": (
                "Minimum query-coverage score (0–100) for an entity to appear "
                "in the BEST MATCH block. Higher = fewer, more confident matches."
            ),
        },
    )
    best_match_max_results: int = Field(
        default=6,
        ge=1,
        le=50,
        title="BEST MATCH max results",
        json_schema_extra={
            "help": "Maximum entities in the merged BEST MATCH block.",
        },
    )
    candidate_limit: int = Field(
        default=50,
        ge=1,
        le=200,
        title="BEST MATCH candidates per type",
        json_schema_extra={
            "help": (
                "Items fetched per entity type per source to feed BEST MATCH "
                "ranking before the score cut-off."
            ),
        },
    )
    ai_suggestions_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        title="AI suggestions per source",
        json_schema_extra={
            "help": "Semantic suggestion tracks requested per source for its card.",
        },
    )


class KalinkaConfig(BaseModel):
    """Main Kalinka configuration."""

    server: ServerConfig = Field(default_factory=ServerConfig, title="Server")
    output: OutputConfig = Field(default_factory=OutputConfig, title="Output")
    input: InputConfig = Field(default_factory=InputConfig, title="Input")
    decoder: DecoderConfig = Field(default_factory=DecoderConfig, title="Decoders")
    fixups: FixupsConfig = Field(default_factory=FixupsConfig, title="Hardware fixups")
    search: SearchConfig = Field(default_factory=SearchConfig, title="Search")
    device_automation: DeviceAutomationConfig = Field(
        default_factory=DeviceAutomationConfig, title="Device automation"
    )

    @classmethod
    def presentation_layout(
        cls, instance: "KalinkaConfig", prefix: str
    ) -> list["SectionSpec"]:
        """Flat General page layout: five peer sections, promoting nested
        BaseModels out of `base_config`.
        """
        from .config_schema_processor import _build_field_spec
        from .presentation_schema import Banner, Importance, SectionSpec, Severity

        def leaf(path_suffix: str):
            parts = path_suffix.split(".")
            model = instance
            for p in parts[:-1]:
                model = getattr(model, p)
            field = model.__class__.model_fields[parts[-1]]
            spec = _build_field_spec(
                f"{prefix}.{path_suffix}", parts[-1], field
            )
            assert spec is not None, path_suffix
            return spec

        srv = SectionSpec(
            id=f"{prefix}.server",
            title="Server",
            fields=[
                leaf("server.service_name"),
                leaf("server.interface"),
                leaf("server.port"),
                leaf("server.log_level"),
            ],
        )

        device_auto = SectionSpec(
            id=f"{prefix}.device_automation",
            title="Device automation",
            fields=[
                leaf("device_automation.auto_power_on"),
                leaf("device_automation.auto_power_off"),
                leaf("device_automation.auto_off_timeout_seconds"),
            ],
        )

        audio_out = SectionSpec(
            id=f"{prefix}.output",
            title="Audio output",
            fields=[
                leaf("output.alsa.device"),
                leaf("output.alsa.latency_ms"),
                leaf("output.alsa.period_ms"),
            ],
        )

        fixups = SectionSpec(
            id=f"{prefix}.fixups",
            title="Hardware fixups",
            banners=[
                Banner(
                    text=(
                        "These settings work around hardware-specific bugs. Leave at "
                        "defaults unless you experience audio glitches on track changes."
                    ),
                    severity=Severity.WARNING,
                )
            ],
            fields=[
                leaf("fixups.alsa_sleep_after_format_setup_ms"),
                leaf("fixups.alsa_reopen_device_with_new_format"),
            ],
        )

        buffers = SectionSpec(
            id=f"{prefix}.buffers",
            title="Buffers & decoders",
            importance=Importance.EXPERT,
            fields=[
                leaf("input.http.buffer_size"),
                leaf("input.http.chunk_size"),
                leaf("decoder.flac.buffer_size"),
                leaf("decoder.mpeg.buffer_size"),
            ],
        )

        search_section = SectionSpec(
            id=f"{prefix}.search",
            title="Search",
            importance=Importance.EXPERT,
            fields=[
                leaf("search.ai_suppress_full_match_score"),
                leaf("search.best_match_min_score"),
                leaf("search.best_match_max_results"),
                leaf("search.candidate_limit"),
                leaf("search.ai_suggestions_limit"),
            ],
        )

        return [srv, device_auto, audio_out, fixups, buffers, search_section]

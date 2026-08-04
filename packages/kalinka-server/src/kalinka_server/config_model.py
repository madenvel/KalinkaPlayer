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

import os
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from kalinka_plugin_sdk import paths

if TYPE_CHECKING:
    from .presentation_schema import SectionSpec


# Shared extras — keeps audit-tagging consistent across the file.
_SIMPLE: dict[str, Any] = {"importance": "simple"}

# Release hosting the MiniLM text encoder (model.onnx + tokenizer.json are
# appended at fetch time). Same release the Jamendo mood index ships from —
# the index is built in this model's embedding space.
_MINILM_RELEASE = (
    "https://github.com/madenvel/KalinkaPlayer/releases/download/jamendo-ai-v1"
)


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
            "help": 'Network connection the server listens on — "all" is right for most setups',
        },
    )
    port: int = Field(
        default=8000,
        title="Port",
        json_schema_extra={
            "help": "Port the app uses to connect to this server",
            "widget": "number_input",
            "constraints": {"ge": 1, "le": 65535},
            **_SIMPLE,
        },
    )
    service_name: str = Field(
        default="My Kalinka Service",
        title="Service name",
        json_schema_extra={
            "help": "How this server appears in the app when found on your network",
            **_SIMPLE,
        },
    )
    log_level: LogLevel = Field(
        default=LogLevel.info,
        title="Log level",
        json_schema_extra=_SIMPLE,
    )
    auto_upgrade: bool = Field(
        default=False,
        title="Auto upgrade",
        json_schema_extra={
            "help": (
                "Install server updates automatically at night (between 3 "
                "and 6 AM) when nothing is playing; the server restarts "
                "itself when done"
            ),
            **_SIMPLE,
        },
    )
    # App-written when the first-run wizard finishes; expert-tier on purpose.
    oobe_complete: bool = Field(
        default=False,
        title="Initial setup complete",
        json_schema_extra={
            "help": (
                "Set when the app's first-run setup wizard finishes — "
                "reset to run the wizard again from a newly connected app"
            ),
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
                "When your search is simply an artist's name, the AI "
                "suggestions row is hidden. This sets how close the match "
                "must be (0–100) — 100 hides it only on an exact name"
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
                "How well a result must match your search (0–100) to appear "
                "under BEST MATCH — higher shows fewer, more confident matches"
            ),
        },
    )
    best_match_max_results: int = Field(
        default=3,
        ge=1,
        le=50,
        title="BEST MATCH max results per source",
        json_schema_extra={
            "help": "How many results each source may show in its BEST MATCH section",
        },
    )
    candidate_limit: int = Field(
        default=50,
        ge=1,
        le=200,
        title="BEST MATCH candidates per type",
        json_schema_extra={
            "help": (
                "How many results are considered when ranking BEST MATCH — "
                "higher is more thorough but slower"
            ),
        },
    )
    ai_suggestions_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        title="AI suggestions per source",
        json_schema_extra={
            "help": "How many tracks each source shows in its AI suggestions row",
        },
    )
    related_max_results: int = Field(
        default=12,
        ge=1,
        le=50,
        title="Related artists max",
        json_schema_extra={
            "help": "How many artists appear in the Related Artists row",
        },
    )
    suggest_min_score: int = Field(
        default=25,
        ge=0,
        le=100,
        title="Search suggestion validation threshold",
        json_schema_extra={
            "help": (
                "How strongly a suggested search must match your library "
                "(0–100) to be offered — higher offers fewer, safer "
                "suggestions"
            ),
        },
    )
    # Catalog routing (query_router.py): shortcuts to browse shelves shown
    # above the search results when the query names one ("recently added").
    # Floor default measured against MiniLM: bare artist names score up to
    # ~0.50 vs shelf titles ("radiohead" vs "Popular Tracks" = 0.49) while
    # real catalog hits score 0.58+ — 55 sits in the gap between the two.
    route_max_results: int = Field(
        default=3,
        ge=1,
        le=10,
        title="Catalog shortcuts max",
        json_schema_extra={
            "help": (
                "How many browse shortcuts (like Recently Added or New "
                "Releases) may appear above the search results"
            ),
        },
    )
    route_min_similarity: int = Field(
        default=55,
        ge=0,
        le=100,
        title="Catalog shortcut minimum similarity",
        json_schema_extra={
            "help": (
                "How closely your search must match a section name (0–100) "
                "for its shortcut to appear — higher shows fewer shortcuts"
            ),
        },
    )
    route_decoy_margin: int = Field(
        default=5,
        ge=0,
        le=100,
        title="Catalog shortcut decisiveness",
        json_schema_extra={
            "help": (
                "How much better a section must fit your search than a "
                "generic music request (0–100) — higher shows shortcuts only "
                "for unmistakable matches"
            ),
        },
    )


class EmbeddingConfig(BaseModel):
    """Shared MiniLM text embedder (kalinka_server.text_embedder) — one model
    instance serving the server and every plugin via the plugin context."""

    model_dir: str = Field(
        # Persistent state dir (<prefix>/var/lib/kalinka), same place the
        # module databases live — NOT the user home, which is read-only on
        # the device. paths.state_dir() honours $KALINKA_PREFIX.
        default_factory=lambda: os.path.join(
            paths.state_dir(), "models", "minilm"
        ),
        title="Text embedding model directory",
        json_schema_extra={
            "help": "Where the shared text-embedding model is stored",
        },
    )
    model_url: str = Field(
        default=_MINILM_RELEASE,
        title="Text embedding model download URL",
        json_schema_extra={
            "help": (
                "Where the model is downloaded from if missing — leave empty "
                "if you manage the files yourself"
            ),
        },
    )


class KalinkaConfig(BaseModel):
    """Main Kalinka configuration."""

    server: ServerConfig = Field(default_factory=ServerConfig, title="Server")
    search: SearchConfig = Field(default_factory=SearchConfig, title="Search")
    embedding: EmbeddingConfig = Field(
        default_factory=EmbeddingConfig, title="Text embedding"
    )
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
        from .presentation_schema import Importance, SectionSpec

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
                leaf("server.auto_upgrade"),
                leaf("server.oobe_complete"),
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
                leaf("search.suggest_min_score"),
                leaf("search.related_max_results"),
                leaf("search.route_max_results"),
                leaf("search.route_min_similarity"),
                leaf("search.route_decoy_margin"),
            ],
        )

        embedding_section = SectionSpec(
            id=f"{prefix}.embedding",
            title="Text embedding",
            importance=Importance.EXPERT,
            fields=[
                leaf("embedding.model_dir"),
                leaf("embedding.model_url"),
            ],
        )

        return [
            srv,
            device_auto,
            search_section,
            embedding_section,
        ]

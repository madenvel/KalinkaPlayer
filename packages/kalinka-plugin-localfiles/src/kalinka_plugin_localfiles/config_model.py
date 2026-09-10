import os
from typing import Any, ClassVar
from pydantic import BaseModel, Field
from kalinka_plugin_sdk import paths
from kalinka_plugin_sdk.module_config import ModuleConfig


# Shared extras. The presentation layer recognises two tiers:
# "simple" (always shown on the structured settings page) and
# "expert" (the default; reachable only via about:config search).
# Mark a field "simple" only when it's mandatory or frequently changed.
_SIMPLE: dict[str, Any] = {"importance": "simple"}

# Independent of the tier: what the app's first-run wizard asks for.
_PROMPT: dict[str, Any] = {"setup": "prompt"}

# What this source is called wherever it is named — the settings page, a
# search heading, a track's attribution.
DISPLAY_NAME = "Local Library"


class MoodConfig(BaseModel):
    """Mood (valence/arousal) ranking.

    Blends a learned V-A axis into AI search so mood queries rank by emotional
    proximity (the axis CLAP is weakest on). Artifacts auto-download with the
    CLAP models; on by default, degrades silently to pure CLAP if unavailable.
    """

    enabled: bool = Field(default=True, title="Enable mood ranking")
    weight: float = Field(
        default=0.6, ge=0.0, le=1.0, title="Mood weight",
        json_schema_extra={
            "help": (
                "How strongly mood matching influences results for mood-style "
                "searches — searches without a mood are unaffected"
            ),
        },
    )
    candidates: int = Field(
        default=200, title="Mood candidates before re-ranking",
        json_schema_extra={
            "help": (
                "How many tracks are considered when ranking by mood — "
                "higher is more thorough but slower"
            ),
        },
    )
    nn_fallback: bool = Field(
        default=True, title="CLAP-text nearest-neighbour fallback",
        json_schema_extra={
            "help": (
                "Guess the intended mood when the search doesn't contain a "
                "known mood word — turn off to match only literal mood words"
            ),
        },
    )
    nn_threshold: float = Field(
        default=0.3, ge=0.0, le=1.0, title="NN fallback confidence threshold",
        json_schema_extra={
            "help": (
                "How confident the mood guess must be before it affects "
                "ranking — below this, results are ranked normally"
            ),
        },
    )
    nn_top_k: int = Field(
        default=3, ge=1, le=10, title="NN fallback neighbours",
    )
    backfill_batch: int = Field(
        default=256, title="Mood backfill batch size",
        json_schema_extra={
            "help": "Tracks processed per pass when computing mood data for the library",
        },
    )


class AiSearchConfig(BaseModel):
    """Semantic search over the library.

    Covers both halves of the feature — embedding the library into CLAP
    vectors, and serving queries against them — because they are useless
    apart: vectors nothing reads, or a query path with nothing to read.

    BEST MATCH (literal name lookup) is not here; it lives server-side in
    ``kalinka_server.SearchConfig``.
    """

    enabled: bool = Field(
        default=False,
        title="Enable AI search",
        json_schema_extra={
            "help": (
                "Search by mood, genre and how the music sounds rather than "
                "by name. Costs about 500 MB of memory while the server runs "
                "and around 750 MB while it indexes, plus hours of CPU to "
                "index a large library the first time. Figures are "
                "approximate and change with the model."
            ),
            **_SIMPLE,
            **_PROMPT,
        },
    )
    max_results: int = Field(
        default=20, title="Max results per entity type",
    )
    knn_candidate_limit: int = Field(
        default=50, title="Max KNN candidates before re-ranking",
    )
    audio_batch_size: int = Field(
        default=4, title="Audio embedding batch size",
    )
    poll_interval_seconds: int = Field(
        default=300,
        title="Poll interval",
        json_schema_extra={"constraints": {"unit": "s"}},
    )
    # The two CLAP towers have different lifecycles. The *text* encoder
    # serves the searcher's query-encode IPC, so it stays resident for the
    # life of the process whenever AI search is enabled — unloading it made
    # the first post-idle search query time out on a ~480 MB reload. The
    # *audio* encoder (~272 MB) is needed only while indexing, so it loads
    # on demand and idles out after the timeout below.
    audio_model_idle_timeout_seconds: int = Field(
        default=900,
        title="Audio model idle timeout",
        json_schema_extra={
            "help": (
                "Free memory by unloading the AI indexing model after this "
                "long with nothing to index (0 = keep loaded)"
            ),
            "constraints": {"unit": "s"},
        },
    )
    max_job_attempts: int = Field(
        default=3, title="Max attempts per embedding job",
    )
    model_dir: str = Field(
        default_factory=lambda: os.path.join(paths.state_dir(), "models"),
        title="Model directory",
        json_schema_extra={"widget": "path"},
    )
    custom_model_dir: str = Field(
        default="",
        title="Override model path",
        # Expected contents: clap_audio_encoder.onnx, clap_text_encoder.onnx,
        # clap_tokenizer.json.
        description=(
            "Folder containing a custom CLAP model. Leave empty to use the "
            "standard model from the model directory."
        ),
        json_schema_extra={"widget": "path"},
    )
    mood: MoodConfig = Field(
        default_factory=MoodConfig, title="Mood ranking"
    )


class MusicBrainzConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable MusicBrainz")
    artist_threshold: int = Field(
        default=90, title="Artist match threshold", ge=0, le=100,
        json_schema_extra={"constraints": {"unit": "%"}},
    )
    album_threshold: int = Field(
        default=90, title="Album match threshold", ge=0, le=100,
        json_schema_extra={"constraints": {"unit": "%"}},
    )
    track_threshold: int = Field(
        default=90, title="Track match threshold", ge=0, le=100,
        json_schema_extra={"constraints": {"unit": "%"}},
    )
    string_similarity: float = Field(
        default=0.8, title="String match similarity", ge=0, le=1,
    )
    debug_matching: bool = Field(
        default=False, title="Enable detailed matching logs",
    )


class AcoustIDConfig(BaseModel):
    """Audio-fingerprint identification. Active exactly when a key is set."""

    api_key: str = Field(
        default="",
        title="AcoustID API key",
        json_schema_extra={
            "help": (
                "Lets Kalinka identify tracks by their audio fingerprint — "
                "get a free key at [acoustid.org](https://acoustid.org/)"
            ),
            "widget": "password",
            **_SIMPLE,
        },
    )


class WikidataConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Wikidata")


class DeezerConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Deezer")


class CoverArtArchiveConfig(BaseModel):
    enabled: bool = Field(
        default=True,
        title="Enable Cover Art Archive",
        json_schema_extra={
            "help": (
                "Fetch album covers from the MusicBrainz Cover Art Archive "
                "for matched releases no other source has art for"
            ),
        },
    )


class ProceduralArtworkConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        title="Generate missing album art",
        json_schema_extra={
            "help": (
                "Draw deterministic abstract cover art for albums that still "
                "have no artwork after all other sources have been tried — "
                "needs the `numpy` package (installed on demand)"
            ),
            **_SIMPLE,
        },
    )


class PluginsConfig(BaseModel):
    musicbrainz: MusicBrainzConfig = Field(
        default_factory=MusicBrainzConfig, title="MusicBrainz"
    )
    acoustid: AcoustIDConfig = Field(default_factory=AcoustIDConfig, title="AcoustID")
    wikidata: WikidataConfig = Field(default_factory=WikidataConfig, title="Wikidata")
    deezer: DeezerConfig = Field(default_factory=DeezerConfig, title="Deezer")
    coverartarchive: CoverArtArchiveConfig = Field(
        default_factory=CoverArtArchiveConfig, title="Cover Art Archive"
    )
    procedural_artwork: ProceduralArtworkConfig = Field(
        default_factory=ProceduralArtworkConfig, title="Generated album art"
    )
    user_agent: str = Field(
        default="Kalinka/1.0 (https://github.com/madenvel/KalinkaPlayer)",
        title="User agent",
    )


class EnricherConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable enricher", json_schema_extra=_SIMPLE,
    )
    concurrency: int = Field(
        default=6,
        ge=1,
        le=16,
        title="Parallel lookups",
        json_schema_extra={
            "help": (
                "How many artists, albums or tracks to look up at once. "
                "Higher values hide the wait between metadata requests, but "
                "each service still enforces its own rate limit, so raising "
                "this past a handful buys little."
            ),
            "constraints": {"min": 1, "max": 16},
        },
    )
    plugins: PluginsConfig = Field(
        default_factory=PluginsConfig, title="Enrichment plugins"
    )


class LocalFilesConfig(ModuleConfig):
    __module_icon__: ClassVar[str] = "folder_outlined"
    __preview_fields__: ClassVar[list[str]] = [
        "music_folders",
        "scan_interval_minutes",
    ]

    name: str = Field(
        default="localfiles",
        title=DISPLAY_NAME,
        description=(
            "Music files on this device or a mounted share, indexed into a "
            "browsable library with artwork and metadata filled in from "
            "MusicBrainz and friends."
        ),
        frozen=True,
        exclude=True,
    )
    enabled: bool = Field(
        default=True, title="Module enabled", json_schema_extra=_SIMPLE,
    )
    music_folders: list[str] = Field(
        # Starter placeholder; users repoint this at their real library. Under
        # /srv (world-writable drop-off, provisioned by the deb postinst) — not
        # /home (kalusr has none) nor the kalusr-only state dir. See media_dir().
        default_factory=lambda: [paths.media_dir()],
        title="Music folders",
        json_schema_extra={"widget": "folder_list", **_SIMPLE, **_PROMPT},
    )
    db_path: str = Field(
        default_factory=lambda: os.path.join(paths.state_dir(), "localfiles.db"),
        title="Database path",
        json_schema_extra={"widget": "path"},
    )
    artwork_path: str = Field(
        default_factory=lambda: os.path.join(paths.cache_dir(), "artwork"),
        title="Artwork cache path",
        json_schema_extra={"widget": "path"},
    )
    scan_interval_minutes: int = Field(
        default=15,
        title="Scan interval",
        json_schema_extra={"constraints": {"unit": "min"}, **_SIMPLE},
    )
    file_watch_enabled: bool = Field(
        default=True,
        title="Enable file watching",
        json_schema_extra={"help": "Rescan on filesystem changes"},
    )
    folder_first_clustering: bool = Field(
        default=True,
        title="Folder-first album grouping",
        json_schema_extra={
            "help": (
                "Group albums from the folder and multiple signals rather than "
                "one album tag per track, so tag variance and untagged rips no "
                "longer fragment an album. Re-clusters the library on the next "
                "scan."
            ),
            **_PROMPT,
        },
    )
    legacy_tag_encoding: str = Field(
        default="",
        title="Legacy tag encoding",
        json_schema_extra={
            "help": (
                "Codepage that repairs garbled tags written by old taggers, "
                "e.g. cp1251 for Cyrillic ('ÐÓÊÈ ÂÂÅÐÕ' -> 'РУКИ ВВЕРХ'). "
                "Leave empty to disable; UTF-8 mojibake is always repaired."
            ),
            **_SIMPLE,
        },
    )
    quiescence_seconds: int = Field(
        default=5,
        title="Upload quiescence window",
        json_schema_extra={
            "help": (
                "Wait until a file has stopped changing for this long before "
                "indexing it, so half-copied uploads aren't picked up"
            ),
            "constraints": {"unit": "s"},
        },
    )
    enricher: EnricherConfig = Field(
        default_factory=EnricherConfig, title="Enricher"
    )
    rebuild_library: bool = Field(
        default=False,
        title="Rebuild library on next restart",
        json_schema_extra={
            "help": (
                "Purge the index and artwork cache and rescan all files on the "
                "next server restart. Finished AI audio analysis is kept and "
                "reused; it re-runs only when the AI model changes. Resets "
                "itself once done."
            ),
            # One-shot trigger: the framework resets this (persist-first)
            # before the plugin acts, so it fires at most once per arming.
            "one_shot": True,
            **_SIMPLE,
        },
    )
    ai_search: AiSearchConfig = Field(
        default_factory=AiSearchConfig, title="AI search"
    )

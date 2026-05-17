from typing import ClassVar
from pydantic import BaseModel, Field
from kalinka_plugin_sdk.module_config import ModuleConfig


# Shared extras. The presentation layer recognises two tiers:
# "simple" (always shown on the structured settings page) and
# "expert" (the default; reachable only via about:config search).
# Mark a field "simple" only when it's mandatory or frequently changed.
_SIMPLE = {"importance": "simple"}


class TagsConfig(BaseModel):
    """Shared tag prediction settings (used by both searcher and embedder).

    Disabled by default. The pipeline depends on essentia-tensorflow,
    which is only published as cp311 ARM wheels and pulls TensorFlow as
    a transitive dep — opt-in keeps the typical install from triggering
    a multi-hundred-megabyte fetch the user didn't ask for, and avoids
    constraining the venv's Python version for everyone. CLAP KNN
    search (embedder) covers most of the value of tag prediction
    without the cost; enable this only if you specifically want the
    Discogs/MIREX/danceability classifiers.
    """

    enabled: bool = Field(
        default=False, title="Enable tag prediction", json_schema_extra=_SIMPLE,
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        title="Minimum tag confidence",
    )
    top_genres: int = Field(
        default=5,
        title="Max genres stored per track",
    )
    effnet_path: str = Field(
        default="",
        title="EffNet-Discogs backbone path",
        json_schema_extra={
            "help": "Auto-downloaded if empty",
            "widget": "path",
        },
    )
    genre_path: str = Field(
        default="",
        title="Genre Discogs400 classifier path",
        json_schema_extra={
            "help": "Auto-downloaded if empty",
            "widget": "path",
        },
    )
    vggish_path: str = Field(
        default="",
        title="VGGish backbone path",
        json_schema_extra={
            "help": "Auto-downloaded if empty",
            "widget": "path",
        },
    )
    mood_mirex_path: str = Field(
        default="",
        title="Mood MIREX classifier path",
        json_schema_extra={
            "help": "Auto-downloaded if empty",
            "widget": "path",
        },
    )
    danceability_path: str = Field(
        default="",
        title="Danceability classifier path",
        json_schema_extra={
            "help": "Auto-downloaded if empty",
            "widget": "path",
        },
    )
    current_version: int = Field(
        default=1,
        title="Model version",
        json_schema_extra={"help": "Increment to force re-tagging"},
    )


class EmbedderClapConfig(BaseModel):
    model_name: str = Field(
        default="laion/clap-htsat-unfused",
        title="CLAP model name",
        json_schema_extra={"help": "HuggingFace model ID"},
    )
    ckpt_path: str = Field(
        default="",
        title="Override model path",
        description=(
            "Directory containing ONNX model files (clap_audio_encoder.onnx, "
            "clap_text_encoder.onnx, clap_tokenizer.json). Uses model_dir if empty."
        ),
        json_schema_extra={"widget": "path"},
    )
    dimensions: int = Field(
        default=512, frozen=True, title="Embedding dimensions"
    )
    current_version: int = Field(
        default=1,
        title="Model version",
        json_schema_extra={"help": "Increment to force re-embedding"},
    )


class AiSearchConfig(BaseModel):
    weight_clap_similarity: float = Field(
        default=0.75, ge=0.0, le=1.0, title="CLAP similarity weight",
    )
    weight_tag_boost: float = Field(
        default=0.15, ge=0.0, le=1.0, title="Tag overlap boost weight",
    )
    weight_popularity: float = Field(
        default=0.10, ge=0.0, le=1.0, title="Popularity weight",
    )
    max_results: int = Field(
        default=20, title="Max results per entity type",
    )
    knn_candidates: int = Field(
        default=50, title="KNN candidates before re-ranking",
    )
    fallback_coverage_threshold: float = Field(
        default=10.0,
        title="Fallback coverage threshold",
        json_schema_extra={
            "help": "Warn if CLAP coverage % is below this",
            "constraints": {"unit": "%"},
        },
    )


class SearcherConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable searcher", json_schema_extra=_SIMPLE,
    )
    tags: TagsConfig = Field(
        default_factory=TagsConfig, title="Tag prediction"
    )
    batch_size_tags: int = Field(
        default=8, title="Tag prediction batch size",
    )
    poll_interval_seconds: int = Field(
        default=300, title="Poll interval",
        json_schema_extra={"constraints": {"unit": "s"}},
    )
    model_idle_timeout_seconds: int = Field(
        default=300,
        title="Model idle timeout",
        json_schema_extra={
            "help": "Unload tag models from memory after this (0 = never unload)",
            "constraints": {"unit": "s"},
        },
    )
    max_job_attempts: int = Field(
        default=3, title="Max attempts per tag job",
    )
    model_dir: str = Field(
        default="/var/lib/kalinka/models",
        title="Model directory",
        json_schema_extra={"widget": "path"},
    )
    weight_fts: float = Field(
        default=0.35, ge=0.0, le=1.0, title="FTS rank weight",
    )
    weight_knn: float = Field(
        default=0.30, ge=0.0, le=1.0, title="CLAP KNN similarity weight",
    )
    weight_genre: float = Field(
        default=0.20, ge=0.0, le=1.0, title="Genre match weight",
    )
    weight_mood: float = Field(
        default=0.10, ge=0.0, le=1.0, title="Mood match weight",
    )
    weight_danceability: float = Field(
        default=0.05, ge=0.0, le=1.0, title="Danceability match weight",
    )
    fts_candidate_limit: int = Field(
        default=100, title="Max FTS candidates before re-ranking",
    )
    knn_candidate_limit: int = Field(
        default=50, title="Max KNN candidates before re-ranking",
    )
    max_results: int = Field(
        default=20, title="Max results per entity type",
    )


class EmbedderConfig(BaseModel):
    # Tag prediction (genre/mood/danceability) is run by the *searcher*,
    # not the embedder — see SearcherConfig.tags. Earlier revisions of
    # this config declared an unused `tags: TagsConfig` and
    # `batch_size_tags` field here, which the presentation schema then
    # rendered as a duplicate "Tag prediction" section on the embedder
    # card. Both fields were dead code in the embedder process; removed.

    enabled: bool = Field(
        default=False, title="Enable embedder", json_schema_extra=_SIMPLE,
    )
    batch_size_clap: int = Field(
        default=4, title="CLAP audio embedding batch size",
    )
    poll_interval_seconds: int = Field(
        default=300,
        title="Poll interval",
        json_schema_extra={"constraints": {"unit": "s"}},
    )
    # NB: CLAP no longer idles out. The model is shared with the searcher's
    # text-encode IPC and unloading made the first post-idle search query
    # time out (~30 s for a model reload). It now stays resident for the
    # lifetime of the embedder process.
    max_job_attempts: int = Field(
        default=3, title="Max attempts per embedding job",
    )
    model_dir: str = Field(
        default="/var/lib/kalinka/models",
        title="Model directory",
        json_schema_extra={"widget": "path"},
    )
    clap: EmbedderClapConfig = Field(
        default_factory=EmbedderClapConfig, title="CLAP audio embedding"
    )
    ai_search: AiSearchConfig = Field(
        default_factory=AiSearchConfig, title="AI search"
    )


class MusicBrainzConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable MusicBrainz", json_schema_extra=_SIMPLE,
    )
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
    enabled: bool = Field(
        default=False, title="Enable AcoustID", json_schema_extra=_SIMPLE,
    )
    api_key: str = Field(
        default="",
        title="AcoustID API key",
        json_schema_extra={"widget": "password", **_SIMPLE},
    )


class WikidataConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable Wikidata", json_schema_extra=_SIMPLE,
    )


class DeezerConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable Deezer", json_schema_extra=_SIMPLE,
    )


class PluginsConfig(BaseModel):
    musicbrainz: MusicBrainzConfig = Field(
        default_factory=MusicBrainzConfig, title="MusicBrainz"
    )
    acoustid: AcoustIDConfig = Field(default_factory=AcoustIDConfig, title="AcoustID")
    wikidata: WikidataConfig = Field(default_factory=WikidataConfig, title="Wikidata")
    deezer: DeezerConfig = Field(default_factory=DeezerConfig, title="Deezer")
    filesystem_fallback_enabled: bool = Field(
        default=True,
        title="Use file name/path for enrichment",
        json_schema_extra=_SIMPLE,
    )

    user_agent: str = Field(
        default="Kalinka/1.0 (https://github.com/madenvel/KalinkaPlayer)",
        title="User agent",
    )


class EnricherConfig(BaseModel):
    enabled: bool = Field(
        default=True, title="Enable enricher", json_schema_extra=_SIMPLE,
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

    name: str = Field(default="localfiles", title="Local files", frozen=True, exclude=True)
    enabled: bool = Field(
        default=True, title="Module enabled", json_schema_extra=_SIMPLE,
    )
    music_folders: list[str] = Field(
        default=["~/Music"],
        title="Music folders",
        json_schema_extra={"widget": "folder_list", **_SIMPLE},
    )
    db_path: str = Field(
        default="/var/lib/kalinka/localfiles.db",
        title="Database path",
        json_schema_extra={"widget": "path"},
    )
    artwork_path: str = Field(
        default="/var/cache/kalinka/artwork",
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
        json_schema_extra={"help": "Rescan on filesystem changes", **_SIMPLE},
    )
    quiescence_seconds: int = Field(
        default=5,
        title="Upload quiescence window",
        json_schema_extra={
            "help": (
                "Defer indexing of files modified within this many seconds. "
                "Protects against indexing partial files during slow uploads."
            ),
            "constraints": {"unit": "s"},
        },
    )
    enricher: EnricherConfig = Field(
        default_factory=EnricherConfig, title="Enricher"
    )
    rescan_on_startup: bool = Field(
        default=False,
        title="Rescan on next restart",
        json_schema_extra={
            "help": "Purges the database and rebuilds it on next server restart",
        },
    )
    searcher: SearcherConfig = Field(
        default_factory=SearcherConfig, title="Searcher"
    )
    embedder: EmbedderConfig = Field(
        default_factory=EmbedderConfig, title="Embedder (AI search)"
    )

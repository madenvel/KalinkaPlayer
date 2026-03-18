from pydantic import BaseModel, Field
from kalinka_plugin_sdk.module_config import ModuleConfig


class EmbedderTagsConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Tag Prediction")
    min_confidence: float = Field(default=0.3, ge=0.0, le=1.0, title="Minimum Tag Confidence")
    top_genres: int = Field(default=5, title="Max genres stored per track")
    effnet_path: str = Field(default="", title="EffNet-Discogs backbone path (auto-downloaded if empty)")
    genre_path: str = Field(default="", title="Genre Discogs400 classifier path (auto-downloaded if empty)")
    vggish_path: str = Field(default="", title="VGGish backbone path (auto-downloaded if empty)")
    mood_mirex_path: str = Field(default="", title="Mood MIREX classifier path (auto-downloaded if empty)")
    danceability_path: str = Field(default="", title="Danceability classifier path (auto-downloaded if empty)")
    current_version: int = Field(default=1, title="Model version — increment to force re-tagging")


class EmbedderClapConfig(BaseModel):
    model_name: str = Field(default="laion/clap-htsat-unfused", title="CLAP model name (HuggingFace)")
    ckpt_path: str = Field(default="", title="Override checkpoint path (auto-downloaded if empty)")
    dimensions: int = Field(default=512, frozen=True, title="Embedding dimensions")
    current_version: int = Field(default=1, title="Model version — increment to force re-embedding")


class AiSearchConfig(BaseModel):
    weight_clap_similarity: float = Field(default=0.75, ge=0.0, le=1.0, title="CLAP similarity weight")
    weight_tag_boost: float = Field(default=0.15, ge=0.0, le=1.0, title="Tag overlap boost weight")
    weight_popularity: float = Field(default=0.10, ge=0.0, le=1.0, title="Popularity weight")
    max_results: int = Field(default=20, title="Max results per entity type")
    knn_candidates: int = Field(default=50, title="KNN candidates before re-ranking")
    fallback_coverage_threshold: float = Field(
        default=10.0, title="Warn if CLAP coverage % is below this threshold"
    )


class EmbedderConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable Embedder")
    batch_size_tags: int = Field(default=8, title="Tag prediction batch size")
    batch_size_clap: int = Field(default=4, title="CLAP audio embedding batch size")
    poll_interval_seconds: int = Field(default=300, title="Poll Interval (seconds)")
    model_idle_timeout_seconds: int = Field(
        default=600,
        title="Model Idle Timeout (seconds)",
        description="Unload models from memory after this many seconds of inactivity (0 = never unload)",
    )
    max_job_attempts: int = Field(default=3, title="Max attempts per embedding job before marking failed")
    model_dir: str = Field(
        default="/var/lib/kalinka/models",
        title="Directory for auto-downloaded model files",
    )
    tags: EmbedderTagsConfig = Field(default_factory=EmbedderTagsConfig, title="Tag Prediction Settings")
    clap: EmbedderClapConfig = Field(default_factory=EmbedderClapConfig, title="CLAP Audio Embedding Settings")
    ai_search: AiSearchConfig = Field(default_factory=AiSearchConfig, title="AI Search Settings")


class MusicBrainzConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable MusicBrainz")
    artist_threshold: int = Field(
        default=90, title="Artist Match Threshold", ge=0, le=100
    )
    album_threshold: int = Field(
        default=90, title="Album Match Threshold", ge=0, le=100
    )
    track_threshold: int = Field(
        default=90, title="Track Match Threshold", ge=0, le=100
    )
    string_similarity: float = Field(
        default=0.8, title="String Match Similarity Threshold", ge=0, le=1
    )
    debug_matching: bool = Field(default=False, title="Enable detailed matching logs")


class AcoustIDConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable AcoustID")
    api_key: str = Field(default="", title="AcoustID API Key")


class WikidataConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Wikidata")


class DeezerConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Deezer")


class PluginsConfig(BaseModel):
    musicbrainz: MusicBrainzConfig = Field(
        default_factory=MusicBrainzConfig, title="MusicBrainz"
    )
    acoustid: AcoustIDConfig = Field(default_factory=AcoustIDConfig, title="AcoustID")
    wikidata: WikidataConfig = Field(default_factory=WikidataConfig, title="Wikidata")
    deezer: DeezerConfig = Field(default_factory=DeezerConfig, title="Deezer")
    filesystem_fallback_enabled: bool = Field(
        default=True, title="Use file name and path for enrichment"
    )

    user_agent: str = Field(
        default="Kalinka/1.0 (https://github.com/madenvel/KalinkaPlayer)",
        title="User Agent",
    )


class EnricherConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Enricher")
    plugins: PluginsConfig = Field(
        default_factory=PluginsConfig, title="Enrichment Plugins"
    )


class LocalFilesConfig(ModuleConfig):
    name: str = Field(default="localfiles", title="Local", frozen=True, exclude=True)
    enabled: bool = Field(default=True, title="Module Enabled")
    music_folders: list[str] = Field(default=["~/Music"], title="Music Folders")
    db_path: str = Field(
        default="/var/lib/kalinka/localfiles.db", title="Database Path"
    )
    artwork_path: str = Field(
        default="/var/cache/kalinka/artwork", title="Artwork Path"
    )
    scan_interval_minutes: int = Field(default=5, title="Scan Interval (min)")
    file_watch_enabled: bool = Field(default=True, title="Enable File Watching")
    enricher: EnricherConfig = Field(
        default_factory=EnricherConfig, title="Enricher Settings"
    )
    rescan_on_startup: bool = Field(
        default=False, title="Purge database and rescan after restart"
    )
    embedder: EmbedderConfig = Field(
        default_factory=EmbedderConfig, title="Embedder Settings"
    )

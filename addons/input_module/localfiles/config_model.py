from pydantic import BaseModel, Field
from src.base_config_model import ModuleConfig
from typing import List


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
    enabled: bool = Field(default=False, title="Module Enabled")
    music_folders: list[str] = Field(default=["~/Music"], title="Music Folders")
    db_path: str = Field(
        default="/var/lib/kalinka/localfiles.db", title="Database Path"
    )
    artwork_path: str = Field(default="/var/lib/kalinka/artwork", title="Artwork Path")
    scan_interval_minutes: int = Field(default=5, title="Scan Interval (min)")
    file_watch_enabled: bool = Field(default=True, title="Enable File Watching")
    enricher: EnricherConfig = Field(
        default_factory=EnricherConfig, title="Enricher Settings"
    )

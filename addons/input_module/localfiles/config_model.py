from pydantic import BaseModel, Field
from src.base_config_model import ModuleConfig
from typing import List


class MusicBrainzConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable MusicBrainz")
    match_threshold: int = Field(
        default=80, title="Match Threshold (0-100)", ge=0, le=100
    )
    user_agent: str = Field(
        default="Kalinka/1.0 (https://github.com/madenvel/KalinkaPlayer)",
        title="User Agent",
    )


class AcoustIDConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable AcoustID")
    api_key: str = Field(default="", title="AcoustID API Key")


class WikidataConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable Wikidata")


class DeezerConfig(BaseModel):
    enabled: bool = Field(default=False, title="Enable Deezer")


class PluginsConfig(BaseModel):
    musicbrainz: MusicBrainzConfig = Field(default_factory=MusicBrainzConfig)
    acoustid: AcoustIDConfig = Field(default_factory=AcoustIDConfig)
    wikidata: WikidataConfig = Field(default_factory=WikidataConfig)
    deezer: DeezerConfig = Field(default_factory=DeezerConfig)


class EnricherConfig(BaseModel):
    enabled: bool = Field(default=True, title="Enable Enricher")
    plugins: PluginsConfig = Field(
        default_factory=PluginsConfig, title="Enrichment Plugins"
    )


class LocalFilesConfig(ModuleConfig):
    name: str = Field(default="localfiles", title="Local", frozen=True)
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

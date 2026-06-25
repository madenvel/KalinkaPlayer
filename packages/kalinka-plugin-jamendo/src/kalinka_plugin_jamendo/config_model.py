from typing import ClassVar
from enum import Enum

from pydantic import ConfigDict, Field

from kalinka_plugin_sdk.module_config import ModuleConfig

# Release hosting the int8 mood index + MiniLM encoder (see kalinka-training
# jamendomaxcaps_embed.py). Files are fetched at startup if absent.
_RELEASE = "https://github.com/madenvel/KalinkaPlayer/releases/download/jamendo-ai-v1"


class JamendoAudioFormat(str, Enum):
    """User-facing audio quality labels.

    The labels are what the settings UI shows; they map to Jamendo's
    ``audioformat`` codes (mp31/mp32/ogg/flac) in ``jamendo.FORMAT_CODE``.
    """

    MP3_VBR = "MP3 (VBR ~V0)"
    MP3_96 = "MP3 (96 kbps)"
    OGG = "OGG Vorbis"
    FLAC = "FLAC"


class JamendoConfig(ModuleConfig):
    """Jamendo input module settings."""

    model_config = ConfigDict(use_enum_values=True)

    __module_icon__: ClassVar[str] = "music_note_outlined"
    __module_icon_color__: ClassVar[str] = "#2E7D32"  # Jamendo green
    __preview_fields__: ClassVar[list[str]] = ["audio_format"]

    name: str = Field(default="jamendo", title="Jamendo", frozen=True, exclude=True)
    client_id: str = Field(
        default="",
        title="Client ID",
        description=(
            "Jamendo API client_id. Required — without it every request "
            "fails. Create a free application to obtain one at: "
            "https://devportal.jamendo.com"
        ),
        json_schema_extra={"widget": "password", "importance": "simple"},
    )
    audio_format: JamendoAudioFormat = Field(
        default=JamendoAudioFormat.MP3_VBR,
        title="Audio quality",
        description=(
            "Streaming format. FLAC is only available for tracks whose "
            "artist allowed lossless download and falls back to MP3 otherwise."
        ),
        json_schema_extra={"importance": "simple"},
    )
    ai_search_enabled: bool = Field(
        default=True,
        title="Mood / AI search",
        description=(
            "Enable natural-language mood/genre search over Jamendo "
            "(e.g. \"something melancholic for tonight\"). Requires the "
            "downloaded mood index and embedding model; silently does "
            "nothing until both are present."
        ),
        json_schema_extra={"importance": "simple"},
    )
    ai_index_path: str = Field(
        default="~/kalinka/jamendo/jamendo_index.sqlite",
        title="Mood index path",
        description="Local path for the sqlite-vec mood index.",
        json_schema_extra={"importance": "expert"},
    )
    ai_index_url: str = Field(
        default=f"{_RELEASE}/jamendo_index.sqlite",
        title="Mood index download URL",
        description="Fetched to the index path on first use if absent. Empty = expect present.",
        json_schema_extra={"importance": "expert"},
    )
    ai_model_dir: str = Field(
        default="~/kalinka/jamendo/minilm",
        title="Embedding model directory",
        description="Local dir for the MiniLM model.onnx + tokenizer.json.",
        json_schema_extra={"importance": "expert"},
    )
    ai_model_url: str = Field(
        default=_RELEASE,
        title="Embedding model download URL",
        description="Base URL; model.onnx + tokenizer.json are appended. Empty = expect present.",
        json_schema_extra={"importance": "expert"},
    )

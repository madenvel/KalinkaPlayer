"""Presentation schema — display-side description of the settings UI.

The storage model (Pydantic config classes) is the source of truth for values and
validation. This module defines the separate *presentation* layer the backend emits
so the client renders unambiguously without re-mapping anything.

Authoring entry points:
    * Per-field via `Field(..., json_schema_extra={"widget": ..., "help": ...,
      "importance": ..., "constraints": ...})`.
    * Per-config-class by declaring class attributes:
          __module_icon__: str          — material icon name for module cards
          __module_icon_color__: str    — hex color for icon tile
          __preview_fields__: list[str] — field names to compose the card subtitle
      Or by implementing::
          @classmethod
          def presentation_layout(cls, instance, prefix: str) -> list[SectionSpec]: ...
      for full control of section grouping (used by KalinkaConfig to flatten
      base_config into peer sections on the General page).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Importance(str, Enum):
    NORMAL = "normal"
    ADVANCED = "advanced"
    EXPERT = "expert"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"


class Widget(str, Enum):
    TEXT = "text"
    PASSWORD = "password"
    PATH = "path"
    URL = "url"
    TOGGLE = "toggle"
    NUMBER_INPUT = "number_input"
    NUMBER_SLIDER = "number_slider"
    ENUM_PILLS = "enum_pills"
    ENUM_DROPDOWN = "enum_dropdown"
    LIST_EDITOR = "list_editor"
    FOLDER_LIST = "folder_list"


class Banner(BaseModel):
    text: str
    severity: Severity = Severity.INFO
    title: Optional[str] = None


class Constraints(BaseModel):
    ge: Optional[float] = None
    le: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    step: Optional[float] = None
    pattern: Optional[str] = None
    unit: Optional[str] = None
    slider_min: Optional[float] = None
    slider_max: Optional[float] = None


class FieldSpec(BaseModel):
    """A single settable leaf in the UI."""

    path: str                       # Dotted storage path, e.g. "base_config.server.port"
    label: str
    widget: Widget
    type: str                       # "bool" | "int" | "float" | "str" | "enum" | "list"
    help: Optional[str] = None
    default: Any = None
    readonly: bool = False
    importance: Importance = Importance.NORMAL
    enum_values: Optional[list[str]] = None
    constraints: Optional[Constraints] = None


class SectionSpec(BaseModel):
    """A labelled group of fields (and optionally nested sub-sections)."""

    id: str
    title: str
    icon: Optional[str] = None
    importance: Importance = Importance.NORMAL
    banners: list[Banner] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)
    sections: list["SectionSpec"] = Field(default_factory=list)


class ModuleSpec(BaseModel):
    """A pluggable input module or output device entry on its page."""

    id: str
    kind: Literal["input_module", "device"]
    title: str
    icon: Optional[str] = None
    icon_color: Optional[str] = None
    status: Optional[Literal["ready", "error", "disabled"]] = None
    error_message: Optional[str] = None
    preview_fields: list[str] = Field(default_factory=list)
    banners: list[Banner] = Field(default_factory=list)
    sections: list[SectionSpec] = Field(default_factory=list)


class PageSpec(BaseModel):
    """A top-level tab in the settings screen."""

    id: str
    title: str
    icon: Optional[str] = None
    banners: list[Banner] = Field(default_factory=list)
    sections: list[SectionSpec] = Field(default_factory=list)
    modules: list[ModuleSpec] = Field(default_factory=list)


class PresentationSchema(BaseModel):
    """Top-level payload of `GET /server/config/schema`."""

    schema_version: str
    pages: list[PageSpec]


SectionSpec.model_rebuild()

"""Dynamic-field declarations for plugin-resolved values in the schema.

A plugin can advertise fields whose value is not stored in its config
model but is computed at request time — e.g. a rich-text status display
of an internal sub-feature, or a choice list whose options depend on
runtime probing (ALSA devices, network interfaces).

The plugin declares these statically via `dynamic_fields()`, and the
server builds a flat registry at plugin load. On every GET /server/config,
the values blob is augmented by calling `resolve_dynamic_field(path)` on
each plugin for its declared subpaths. The schema marks these fields with
`dynamic=True` so the UI knows to render them read-only.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DynamicFieldDecl(BaseModel):
    """Static declaration of a dynamic field a plugin owns.

    Keyed by the plugin's internal subpath (e.g. "searcher.status"). The
    server forms the full dotted path by prefixing with the plugin's
    module path (e.g. "input_modules.localfiles.").
    """

    model_config = ConfigDict(frozen=True)

    section_id: str = Field(
        ...,
        description="Relative section path inside the module where the field "
        "is rendered. Empty string places it at the module root. Example: "
        "'searcher' to drop the field into the searcher sub-section.",
    )
    label: str = Field(..., description="Field label shown to the user.")
    widget: str = Field(
        default="text",
        description="Widget name (matches presentation_schema.Widget values, "
        "e.g. 'text' for plain text or 'rich_text' for markdown).",
    )
    value_type: str = Field(
        default="str",
        description="Wire type of the resolved value: 'str', 'list[str]', etc.",
    )
    help: Optional[str] = Field(
        default=None, description="Inline help/description for the field."
    )
    importance: str = Field(
        default="simple",
        description=(
            "Display tier: 'simple' (always shown on the structured "
            "settings page; the default for dynamic fields, since they "
            "act as status displays for their parent enable toggle) "
            "or 'expert' (hidden behind the about:config search). The "
            "legacy values 'normal'/'advanced' are accepted as synonyms."
        ),
    )

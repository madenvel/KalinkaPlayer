"""Schema emitter: walks Pydantic config models to build the presentation schema
and a flat values dict for the wire.

The old nested "{type, title, fields}" wire format has been replaced by two
independent payloads:

    GET /server/config/schema  → PresentationSchema (pages/sections/fields)
    GET /server/config         → {"schema_version", "values": flat dotted-path dict}
    PUT /server/config         → {"schema_version", "changes": {path: value}}

A monotonic `schema_version` string lets the client detect staleness after
plugin reloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import Enum
from typing import Any, Dict, List, Union, get_origin, get_args

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from kalinka_plugin_sdk.module_config import ModuleConfig

from .presentation_schema import (
    Banner,
    Constraints,
    FieldSpec,
    Importance,
    ModuleSpec,
    PageSpec,
    PresentationSchema,
    SectionSpec,
    Severity,
    Widget,
)

logger = logging.getLogger(__name__.split(".")[-1])


# ---------------------------------------------------------------------------
# Type utilities
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def annotation_to_type(annotation: Any) -> str:
    """Map a Pydantic annotation to a simple wire type string."""
    annotation = _unwrap_optional(annotation)

    origin = get_origin(annotation)
    if origin in (list, List):
        args = get_args(annotation)
        inner = annotation_to_type(args[0]) if args else "str"
        return f"list[{inner}]"

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return "section"
        if issubclass(annotation, Enum):
            return "enum"
        if annotation.__module__ == "builtins":
            return annotation.__name__
        return f"{annotation.__module__}.{annotation.__qualname__}"

    return str(annotation)


def _json_extra(field: FieldInfo) -> dict[str, Any]:
    extra = getattr(field, "json_schema_extra", None)
    return extra if isinstance(extra, dict) else {}


def _extract_constraints(field: FieldInfo, extras: dict[str, Any]) -> Constraints | None:
    c = Constraints()
    found = False
    for m in field.metadata or []:
        for attr in ("ge", "gt", "le", "lt", "min_length", "max_length"):
            if hasattr(m, attr):
                val = getattr(m, attr)
                if val is None:
                    continue
                if attr == "gt":
                    c.ge = val
                elif attr == "lt":
                    c.le = val
                else:
                    setattr(c, attr, val)
                found = True
    extra_c = extras.get("constraints") or {}
    for k, v in extra_c.items():
        if hasattr(c, k):
            setattr(c, k, v)
            found = True
    return c if found else None


def _infer_widget(wire_type: str, field_name: str, extras: dict[str, Any]) -> Widget:
    # Explicit opt-in from the model takes precedence
    widget_hint = extras.get("widget")
    if widget_hint:
        try:
            return Widget(widget_hint)
        except ValueError:
            logger.warning("Unknown widget %r on field %s", widget_hint, field_name)

    # Back-compat: `password: True` extra used by qobuz config today
    if extras.get("password") is True:
        return Widget.PASSWORD

    if wire_type == "bool":
        return Widget.TOGGLE
    if wire_type == "enum":
        return Widget.ENUM_PILLS
    if wire_type in ("int", "float"):
        return Widget.NUMBER_INPUT
    if wire_type.startswith("list["):
        return Widget.LIST_EDITOR
    return Widget.TEXT


def _importance_from_extras(extras: dict[str, Any]) -> Importance:
    hint = extras.get("importance")
    if hint is None:
        return Importance.NORMAL
    try:
        return Importance(hint)
    except ValueError:
        logger.warning("Unknown importance %r", hint)
        return Importance.NORMAL


def _enum_values(field: FieldInfo) -> list[str] | None:
    ann = _unwrap_optional(field.annotation)
    if isinstance(ann, type) and issubclass(ann, Enum):
        return [e.value for e in ann.__members__.values()]
    return None


# ---------------------------------------------------------------------------
# Leaf → FieldSpec
# ---------------------------------------------------------------------------


def _build_field_spec(
    path: str, field_name: str, field: FieldInfo
) -> FieldSpec | None:
    """Turn a single non-section Pydantic field into a FieldSpec. Returns None
    if the field is excluded from serialization (e.g. internal `name`).
    """
    if field.exclude:
        return None

    wire_type = annotation_to_type(field.annotation)
    if wire_type == "section":
        raise ValueError(f"_build_field_spec called on section {field_name}")

    extras = _json_extra(field)
    return FieldSpec(
        path=path,
        label=field.title or field_name,
        help=extras.get("help") or field.description or None,
        widget=_infer_widget(wire_type, field_name, extras),
        type=wire_type,
        default=field.default if field.default is not None else None,
        readonly=bool(field.frozen),
        importance=_importance_from_extras(extras),
        enum_values=_enum_values(field) if wire_type == "enum" else None,
        constraints=_extract_constraints(field, extras),
    )


# ---------------------------------------------------------------------------
# Auto-derived section layout (when a config class does not override)
# ---------------------------------------------------------------------------


def _auto_sections(model: BaseModel, prefix: str) -> list[SectionSpec]:
    """Default layout: scalars of this model go into a single 'General' section;
    each nested BaseModel becomes its own section (recursively).
    """
    scalar_fields: list[FieldSpec] = []
    nested_sections: list[SectionSpec] = []

    for field_name, field in model.__class__.model_fields.items():
        wire_type = annotation_to_type(field.annotation)
        child_path = f"{prefix}.{field_name}" if prefix else field_name

        if wire_type == "section":
            nested_model = getattr(model, field_name)
            sub_sections = _sections_for(nested_model, child_path)
            extras = _json_extra(field)
            section_importance = _importance_from_extras(extras)

            # Absorb the child's "General" auto-section (if any) onto the parent
            # wrapper so a BaseModel with both scalars and nested models renders
            # as: <title>{scalars...} + <sub-sections> rather than adding a
            # redundant "General" wrapper just for the scalars.
            promoted_fields: list[FieldSpec] = []
            kept_sections: list[SectionSpec] = []
            for s in sub_sections:
                if s.id == f"{child_path}.general" and not s.sections:
                    promoted_fields = s.fields
                else:
                    kept_sections.append(s)

            nested_sections.append(
                SectionSpec(
                    id=child_path,
                    title=field.title or field_name,
                    importance=section_importance,
                    fields=promoted_fields,
                    sections=kept_sections,
                )
            )
        else:
            spec = _build_field_spec(child_path, field_name, field)
            if spec is not None:
                scalar_fields.append(spec)

    result: list[SectionSpec] = []
    if scalar_fields:
        result.append(
            SectionSpec(
                id=f"{prefix}.general" if prefix else "general",
                title="General",
                fields=scalar_fields,
            )
        )
    result.extend(nested_sections)
    return result


def _sections_for(model: BaseModel, prefix: str) -> list[SectionSpec]:
    """Return sections for a config class, honoring a `presentation_layout`
    override if defined; otherwise auto-derive.
    """
    override = getattr(model.__class__, "presentation_layout", None)
    if callable(override):
        try:
            return list(override(model, prefix))
        except Exception as exc:
            logger.exception("presentation_layout(%s) failed: %s", prefix, exc)
    return _auto_sections(model, prefix)


# ---------------------------------------------------------------------------
# Module/device → ModuleSpec
# ---------------------------------------------------------------------------


def _module_spec(
    config: ModuleConfig,
    kind: str,
    *,
    status: str,
    error_message: str | None,
    path_prefix: str,
) -> ModuleSpec:
    cls = config.__class__
    prefix = f"{path_prefix}.{config.name}"
    sections = _sections_for(config, prefix)

    title = cls.model_fields["name"].title or config.name
    banners_raw = getattr(cls, "__module_banners__", [])
    banners = [b if isinstance(b, Banner) else Banner(**b) for b in banners_raw]

    return ModuleSpec(
        id=config.name,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        icon=getattr(cls, "__module_icon__", None),
        icon_color=getattr(cls, "__module_icon_color__", None),
        status=status,  # type: ignore[arg-type]
        error_message=error_message,
        preview_fields=list(getattr(cls, "__preview_fields__", [])),
        banners=banners,
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Flat values emitter
# ---------------------------------------------------------------------------


def _flatten_values(model: BaseModel, prefix: str, out: dict[str, Any]) -> None:
    for field_name, field in model.__class__.model_fields.items():
        if field.exclude:
            continue
        path = f"{prefix}.{field_name}" if prefix else field_name
        value = getattr(model, field_name, None)
        if isinstance(value, BaseModel):
            _flatten_values(value, path, out)
        elif isinstance(value, Enum):
            out[path] = value.value
        else:
            out[path] = value


def build_values(
    base_config: BaseModel,
    input_modules: dict[str, ModuleConfig],
    devices: dict[str, ModuleConfig],
) -> dict[str, Any]:
    """Return flat `{dotted_path: value}` for every writable field."""
    out: dict[str, Any] = {}
    _flatten_values(base_config, "base_config", out)
    for name, module in input_modules.items():
        _flatten_values(module, f"input_modules.{name}", out)
    for name, device in devices.items():
        _flatten_values(device, f"devices.{name}", out)
    return out


# ---------------------------------------------------------------------------
# Set/get by dotted path (no "root." / ".fields." prefixes)
# ---------------------------------------------------------------------------


def set_field_value(model: BaseModel, field_path: List[str], value: Any) -> None:
    current = model
    for part in field_path[:-1]:
        current = getattr(current, part)
    setattr(current, field_path[-1], value)


def get_field_value(model: BaseModel, field_path: List[str]) -> Any:
    current = model
    for part in field_path:
        current = getattr(current, part)
    return current


# ---------------------------------------------------------------------------
# Top-level presentation schema
# ---------------------------------------------------------------------------


def build_presentation(
    base_config: BaseModel,
    input_modules: dict[str, ModuleConfig],
    devices: dict[str, ModuleConfig],
    *,
    input_modules_with_errors: dict[str, tuple[ModuleConfig, str]] | None = None,
    devices_with_errors: dict[str, tuple[ModuleConfig, str]] | None = None,
) -> PresentationSchema:
    input_modules_with_errors = input_modules_with_errors or {}
    devices_with_errors = devices_with_errors or {}

    # General page: let KalinkaConfig.presentation_layout() shape the sections
    general_sections = _sections_for(base_config, "base_config")
    general_banners_raw = getattr(base_config.__class__, "__page_banners__", [])
    general_banners = [
        b if isinstance(b, Banner) else Banner(**b) for b in general_banners_raw
    ]

    general_page = PageSpec(
        id="general",
        title="General",
        banners=general_banners,
        sections=general_sections,
    )

    # Modules page
    module_specs: list[ModuleSpec] = []
    for name, m in input_modules.items():
        module_specs.append(
            _module_spec(
                m,
                kind="input_module",
                status="ready",
                error_message=None,
                path_prefix="input_modules",
            )
        )
    for name, (m, err) in input_modules_with_errors.items():
        module_specs.append(
            _module_spec(
                m,
                kind="input_module",
                status="error",
                error_message=err,
                path_prefix="input_modules",
            )
        )

    modules_page = PageSpec(id="modules", title="Input modules", modules=module_specs)

    # Devices page
    device_specs: list[ModuleSpec] = []
    for name, d in devices.items():
        device_specs.append(
            _module_spec(
                d,
                kind="device",
                status="ready",
                error_message=None,
                path_prefix="devices",
            )
        )
    for name, (d, err) in devices_with_errors.items():
        device_specs.append(
            _module_spec(
                d,
                kind="device",
                status="error",
                error_message=err,
                path_prefix="devices",
            )
        )

    devices_page = PageSpec(
        id="devices",
        title="Devices",
        banners=[
            Banner(
                text=(
                    "Devices are auto-discovered. Configuration changes require a "
                    "server restart."
                ),
                severity=Severity.INFO,
            )
        ],
        modules=device_specs,
    )

    pages = [general_page, modules_page, devices_page]

    # schema_version: stable hash of the presentation content (excluding itself).
    blob = json.dumps(
        [p.model_dump(mode="json") for p in pages], sort_keys=True, default=str
    )
    schema_version = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    return PresentationSchema(schema_version=schema_version, pages=pages)

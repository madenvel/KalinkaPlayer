"""User configuration overrides.

A single JSON file holds only the values the user has explicitly set,
keyed by the same dotted paths the ``/server/config`` PUT endpoint
accepts (``base_config.*``, ``input_modules.<name>.*``,
``devices.<name>.*``). Everything not in this file falls back to the
code defaults, so default-value changes ship cleanly with a new release.

Loaded once at startup and rewritten whenever the PUT endpoint mutates
state. Atomic-write via tempfile + ``os.replace`` so a crash mid-write
cannot leave a half-written file in place.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Mapping, get_args

from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__.split(".")[-1])


def _set_by_path(model: BaseModel, attrs: List[str], value: Any) -> None:
    # Mirrors config_schema_processor.set_field_value; inlined to avoid a
    # circular import (config_schema_processor pulls in player_setup,
    # which now imports this module).
    current: Any = model
    for part in attrs[:-1]:
        current = getattr(current, part)
    field = attrs[-1]
    # Validate/coerce the value against the target field's declared type
    # before assigning. A plain setattr would silently store a
    # type-invalid override (e.g. port="abc") as the wrong type and blow
    # up later in unrelated code; instead let the resulting ValidationError
    # (a ValueError) propagate so the caller logs and skips it. Coercion
    # also normalizes JSON-decoded values (e.g. "9001" -> 9001).
    fields = getattr(type(current), "model_fields", None)
    if fields is not None and field in fields:
        annotation = fields[field].annotation
        if annotation is not None:
            value = TypeAdapter(annotation).validate_python(value)
    setattr(current, field, value)


def load_overrides(path: str) -> Dict[str, Any]:
    """Read overrides from disk. Missing/unreadable file yields ``{}``."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Cannot read overrides file %s: %s. Starting with defaults.",
            path,
            exc,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "Overrides file %s is not a JSON object. Ignoring.", path
        )
        return {}
    return data


def save_overrides(path: str, overrides: Mapping[str, Any]) -> None:
    """Atomically rewrite the overrides file."""
    config_dir = os.path.dirname(path)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".overrides-", suffix=".tmp", dir=config_dir or "."
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(dict(overrides), f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_path(model: BaseModel, attrs: List[str]) -> Any:
    current: Any = model
    for part in attrs:
        current = getattr(current, part)
    return current


def _model_from_annotation(annotation: Any) -> type[BaseModel] | None:
    """Resolve a field annotation to its BaseModel class, unwrapping
    Optional/Union (e.g. ``SubModel | None`` → ``SubModel``). None if the
    annotation isn't (or doesn't wrap) a BaseModel."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def is_one_shot_field(model_cls: type[BaseModel], attrs: List[str]) -> bool:
    """True if the field at dotted ``attrs`` on ``model_cls`` is declared a
    one-shot trigger via ``json_schema_extra={"one_shot": True}``.

    A one-shot field is a "do X once on the next restart" toggle: set via the
    normal config PUT, acted on a single time at startup, then reset by the
    framework. Walks nested pydantic models (including Optional ones) so
    ``sub.flag`` works too.
    """
    cls: Any = model_cls
    info = None
    for part in attrs:
        fields = getattr(cls, "model_fields", None)
        if not fields or part not in fields:
            return False
        info = fields[part]
        cls = _model_from_annotation(info.annotation)
    if info is None:
        return False
    extra = info.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("one_shot"))


def find_one_shot_overrides(
    model_cls: type[BaseModel],
    prefix: str,
    config: BaseModel,
    overrides: Mapping[str, Any],
) -> List[str]:
    """Return the override keys under ``prefix`` that target a one-shot field
    and are currently *armed* — i.e. their value on ``config`` differs from
    the field's default.

    Pure (no mutation): the caller resets these persist-first before the
    plugin acts, so an armed trigger fires at most once and never repeats on
    the following boot.
    """
    default = model_cls()
    armed: List[str] = []
    for key in overrides:
        if not key.startswith(prefix):
            continue
        attrs = key[len(prefix):].split(".")
        if not is_one_shot_field(model_cls, attrs):
            continue
        try:
            if _read_path(config, attrs) != _read_path(default, attrs):
                armed.append(key)
        except (AttributeError, IndexError, TypeError):
            continue
    return armed


def apply_overrides_with_prefix(
    model: BaseModel,
    overrides: Mapping[str, Any],
    prefix: str,
) -> None:
    """Apply every override whose key starts with ``prefix`` to ``model``.

    Invalid paths or values are logged and skipped — the overrides file
    can outlive schema renames, and we'd rather start with the wrong
    value missing than refuse to boot.
    """
    for key, value in overrides.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        if not suffix:
            continue
        attrs = suffix.split(".")
        try:
            _set_by_path(model, attrs, value)
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            logger.warning(
                "Ignoring override '%s' (cannot apply): %s", key, exc
            )

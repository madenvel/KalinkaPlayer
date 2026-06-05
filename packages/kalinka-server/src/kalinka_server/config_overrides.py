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
from typing import Any, Dict, List, Mapping

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

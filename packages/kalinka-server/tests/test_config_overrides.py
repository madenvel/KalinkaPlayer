"""Tests for the user-overrides config layer.

Covers the round-trip on disk, atomic rewrite semantics, and the
``apply_overrides_with_prefix`` helper that's used both for the base
config and per-plugin loads.
"""

import json
import os

import pytest
from pydantic import BaseModel, Field

from kalinka_server.config_overrides import (
    apply_overrides_with_prefix,
    load_overrides,
    save_overrides,
)


# --------------------------------------------------------------------------
# Sample models — mirror the real shape (nested BaseModel) without dragging
# in the entire KalinkaConfig graph.
# --------------------------------------------------------------------------


class _Inner(BaseModel):
    port: int = Field(default=8000)
    name: str = Field(default="default")


class _Outer(BaseModel):
    server: _Inner = Field(default_factory=_Inner)
    log_level: str = Field(default="info")


# --------------------------------------------------------------------------
# load_overrides / save_overrides
# --------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path):
    assert load_overrides(str(tmp_path / "absent.cfg")) == {}


def test_load_corrupted_file_returns_empty(tmp_path):
    p = tmp_path / "bad.cfg"
    p.write_text("not valid json {")
    assert load_overrides(str(p)) == {}


def test_load_non_object_returns_empty(tmp_path):
    """A JSON file whose root isn't an object is treated as unusable."""
    p = tmp_path / "list.cfg"
    p.write_text("[1, 2, 3]")
    assert load_overrides(str(p)) == {}


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "overrides.cfg"
    payload = {
        "base_config.server.port": 9001,
        "input_modules.localfiles.enabled": False,
        "input_modules.localfiles.scan_paths": ["/music", "/audiobooks"],
    }
    save_overrides(str(p), payload)
    assert load_overrides(str(p)) == payload


def test_save_is_atomic_no_tempfile_left_behind(tmp_path):
    """``save_overrides`` writes via tempfile + rename; nothing leftover."""
    p = tmp_path / "overrides.cfg"
    save_overrides(str(p), {"base_config.server.port": 8001})
    leftovers = [
        entry.name
        for entry in tmp_path.iterdir()
        if entry.name != p.name
    ]
    assert leftovers == [], f"unexpected files: {leftovers}"


def test_save_overwrites_existing_file(tmp_path):
    p = tmp_path / "overrides.cfg"
    save_overrides(str(p), {"base_config.server.port": 1})
    save_overrides(str(p), {"base_config.server.port": 2})
    assert load_overrides(str(p)) == {"base_config.server.port": 2}


def test_save_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "subdir" / "overrides.cfg"
    save_overrides(str(nested), {"base_config.server.port": 8002})
    assert nested.is_file()


def test_saved_file_is_human_readable_json(tmp_path):
    """The on-disk format is a flat dotted-path → value map, pretty-printed."""
    p = tmp_path / "overrides.cfg"
    save_overrides(str(p), {"b": 2, "a": 1})
    raw = p.read_text()
    # Keys are sorted for stable diffs.
    a_pos = raw.index('"a"')
    b_pos = raw.index('"b"')
    assert a_pos < b_pos
    # Pretty-printed (indented), not minified.
    assert "\n" in raw
    # Round-trips through stdlib json.
    assert json.loads(raw) == {"a": 1, "b": 2}


# --------------------------------------------------------------------------
# apply_overrides_with_prefix
# --------------------------------------------------------------------------


def test_apply_simple_path():
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg, {"base_config.log_level": "debug"}, "base_config."
    )
    assert cfg.log_level == "debug"


def test_apply_nested_path():
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg, {"base_config.server.port": 9090}, "base_config."
    )
    assert cfg.server.port == 9090


def test_apply_ignores_other_prefixes():
    """A `base_config.` override must not bleed into a plugin model."""
    cfg = _Outer()
    overrides = {
        "input_modules.localfiles.server.port": 9999,
        "base_config.server.port": 1234,
    }
    apply_overrides_with_prefix(cfg, overrides, "input_modules.localfiles.")
    assert cfg.server.port == 9999  # plugin-prefix applied
    apply_overrides_with_prefix(cfg, overrides, "base_config.")
    assert cfg.server.port == 1234  # base override wins on its prefix


def test_apply_skips_unknown_paths_without_raising():
    """Stale overrides (field removed in a newer release) are logged, skipped."""
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg,
        {
            "base_config.server.port": 8500,
            "base_config.deleted_field": "gone",
            "base_config.server.nonexistent.deeply.nested": True,
        },
        "base_config.",
    )
    assert cfg.server.port == 8500
    # Other fields untouched at defaults.
    assert cfg.log_level == "info"


def test_apply_coerces_value_to_field_type():
    """A JSON-decoded string for an int field is coerced, not stored raw."""
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg, {"base_config.server.port": "9001"}, "base_config."
    )
    assert cfg.server.port == 9001
    assert isinstance(cfg.server.port, int)


def test_apply_skips_type_invalid_value_without_raising():
    """A value that can't coerce to the field type is logged and skipped,
    leaving the field at its default rather than storing the wrong type."""
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg,
        {
            "base_config.server.port": "not-a-number",
            "base_config.log_level": "debug",
        },
        "base_config.",
    )
    # Bad value rejected; field untouched at its default.
    assert cfg.server.port == 8000
    # A valid sibling override in the same batch still applies.
    assert cfg.log_level == "debug"


def test_apply_with_no_matching_prefix_is_noop():
    cfg = _Outer()
    apply_overrides_with_prefix(
        cfg, {"devices.foo.bar": 1}, "input_modules."
    )
    assert cfg == _Outer()


def test_apply_empty_overrides_is_noop():
    cfg = _Outer()
    apply_overrides_with_prefix(cfg, {}, "base_config.")
    assert cfg == _Outer()


# --------------------------------------------------------------------------
# End-to-end: user PUTs a value, server restarts, value sticks
# --------------------------------------------------------------------------


def test_full_flow_save_then_load_then_apply(tmp_path):
    """Simulates: PUT writes overrides; next boot loads + applies them."""
    overrides_path = tmp_path / "kalinka_conf.cfg"
    save_overrides(
        str(overrides_path),
        {
            "base_config.server.port": 9100,
            "base_config.server.name": "from-rest",
        },
    )

    cfg = _Outer()
    loaded = load_overrides(str(overrides_path))
    apply_overrides_with_prefix(cfg, loaded, "base_config.")

    assert cfg.server.port == 9100
    assert cfg.server.name == "from-rest"

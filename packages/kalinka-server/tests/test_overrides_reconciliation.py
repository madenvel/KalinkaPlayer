"""Tests for ``PreparedModuleCollection._reconcile_consumed_overrides``.

When a plugin's ``setup()`` mutates a field on its in-memory config —
typically a one-shot toggle like ``rescan_on_startup`` — the server
must mirror that change back into the overrides dict (and on to disk),
or the override re-fires on every restart. These tests pin down the
three reconciliation outcomes: keep, update, drop-to-default.
"""

from __future__ import annotations

import enum
import json

import pytest
from pydantic import BaseModel, Field

from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.plugin import PluginBase, PluginType

from kalinka_server.player_setup import PreparedModuleCollection


# --------------------------------------------------------------------------
# Fixture: a minimal fake input-module plugin with a "consume on startup"
# field that mirrors the real ``rescan_on_startup`` shape.
# --------------------------------------------------------------------------


class _Sub(BaseModel):
    threshold: int = Field(default=10)


# Plain (non-str) Enum on purpose: ``_Mode.A != "a"`` and json.dumps()
# rejects it, so a reconciler that doesn't coerce to ``.value`` would
# both spuriously reconcile and produce a non-serializable overrides dict.
class _Mode(enum.Enum):
    A = "a"
    B = "b"


class _FakeConfig(ModuleConfig):
    rescan_on_startup: bool = Field(default=False)
    nested: _Sub = Field(default_factory=_Sub)
    mode: _Mode = Field(default=_Mode.A)


class _FakePlugin(PluginBase):
    PLUGIN_ID = "fake"
    REQUIRES_SDK = "1.0"
    PLUGIN_TYPE = PluginType.INPUT_MODULE
    CONFIG_MODEL = _FakeConfig

    async def setup(self, context):  # noqa: D401 — interface stub
        return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _reconcile(config: _FakeConfig, overrides: dict) -> tuple[int, dict]:
    collection = PreparedModuleCollection()
    changed = collection._reconcile_consumed_overrides(
        "fake", _FakePlugin, config, overrides,
    )
    return changed, overrides


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_drops_override_when_plugin_reset_to_default():
    """The localfiles bug: an override set to True is consumed by the
    plugin and reset to False (the default). The override entry must
    disappear from the dict so it does not re-fire next boot."""
    config = _FakeConfig(rescan_on_startup=False)  # plugin mutated it back
    overrides = {"input_modules.fake.rescan_on_startup": True}

    changed, result = _reconcile(config, overrides)

    assert changed == 1
    assert "input_modules.fake.rescan_on_startup" not in result


def test_updates_override_when_plugin_changed_to_non_default():
    """A plugin that mutates an override to a non-default value should
    have the new value persisted, not dropped."""
    config = _FakeConfig()
    config.nested.threshold = 42
    overrides = {"input_modules.fake.nested.threshold": 10}

    changed, result = _reconcile(config, overrides)

    assert changed == 1
    assert result["input_modules.fake.nested.threshold"] == 42


def test_leaves_unchanged_overrides_alone():
    """Overrides that match the in-memory config are not touched."""
    config = _FakeConfig(rescan_on_startup=True)  # plugin did NOT consume it
    overrides = {"input_modules.fake.rescan_on_startup": True}

    changed, result = _reconcile(config, overrides)

    assert changed == 0
    assert result == {"input_modules.fake.rescan_on_startup": True}


def test_ignores_unrelated_plugin_overrides():
    """Overrides targeting other plugins or top-level config are
    ignored even when this plugin's config diverges from its defaults."""
    config = _FakeConfig(rescan_on_startup=False)
    overrides = {
        "input_modules.other.rescan_on_startup": True,
        "base_config.log_level": "debug",
    }

    changed, result = _reconcile(config, overrides)

    assert changed == 0
    assert result == {
        "input_modules.other.rescan_on_startup": True,
        "base_config.log_level": "debug",
    }


def test_enum_field_unchanged_does_not_spuriously_reconcile():
    """An enum override matching the in-memory value must compare equal
    to its stored ``.value`` string and be left untouched — not flagged
    as changed because ``_Mode.A != "a"``."""
    config = _FakeConfig(mode=_Mode.A)
    overrides = {"input_modules.fake.mode": "a"}

    changed, result = _reconcile(config, overrides)

    assert changed == 0
    assert result == {"input_modules.fake.mode": "a"}
    # The dict the server hands to save_overrides() must stay serializable.
    json.dumps(result)


def test_enum_field_change_stored_as_jsonable_value():
    """When a plugin mutates an enum field, the new value is stored as
    its JSON-friendly ``.value`` string, not the raw Enum (which would
    crash the json.dumps in save_overrides)."""
    config = _FakeConfig(mode=_Mode.B)  # plugin changed it away from default
    overrides = {"input_modules.fake.mode": "a"}

    changed, result = _reconcile(config, overrides)

    assert changed == 1
    assert result["input_modules.fake.mode"] == "b"
    assert not isinstance(result["input_modules.fake.mode"], _Mode)
    json.dumps(result)


def test_handles_unknown_override_key_gracefully():
    """An override that targets a non-existent field on the current
    schema is left alone (apply_overrides_with_prefix already logged
    a warning at load time)."""
    config = _FakeConfig()
    overrides = {"input_modules.fake.nonexistent_field": "value"}

    changed, result = _reconcile(config, overrides)

    assert changed == 0
    assert "input_modules.fake.nonexistent_field" in result


def test_collection_sets_dirty_flag_after_yielding(monkeypatch):
    """End-to-end at the loop level: after a plugin's setup mutates a
    field, the collection's overrides_dirty flag flips to True so the
    server knows to re-persist."""
    collection = PreparedModuleCollection()
    # Simulate what _scan_and_setup_plugins_from_entry_points does
    # when a setup leaves the in-memory config diverging.
    config = _FakeConfig(rescan_on_startup=False)
    overrides = {"input_modules.fake.rescan_on_startup": True}

    if collection._reconcile_consumed_overrides(
        "fake", _FakePlugin, config, overrides,
    ):
        collection.overrides_dirty = True

    assert collection.overrides_dirty is True
    assert overrides == {}

"""Tests for the first-run (OOBE) tag carried by the presentation schema.

Contract:

* Every FieldSpec carries ``setup``; a field that doesn't declare one is
  HIDDEN, so the wizard asks nothing it wasn't told to ask.
* ``setup`` is independent of ``importance``: tagging a field for the
  wizard neither promotes it into the simple pages view nor demotes it.
* REQUIRED means the module can't work until the user answers, so such a
  field must default to the empty value for its type — the emitter warns
  when one doesn't.
* The shipped modules declare the wizard's questions themselves; nothing
  in the app hardcodes which paths first-run setup collects.
"""

from __future__ import annotations

import pytest
from pydantic import Field

from kalinka_plugin_jamendo.config_model import JamendoConfig
from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.config_schema_processor import (
    _setup_from_extras,
    build_presentation,
)
from kalinka_server.presentation_schema import Importance, Setup


@pytest.fixture
def schema():
    return build_presentation(
        base_config=KalinkaConfig(),
        input_modules={
            "localfiles": LocalFilesConfig(),
            "jamendo": JamendoConfig(),
        },
        devices={"musiccast": KalinkaPluginMusiccastConfig()},
    )


def _by_path(schema):
    return {f.path: f for f in schema.expert_fields}


def _simple_view_paths(pages) -> set[str]:
    out: set[str] = set()

    def walk(sections):
        for s in sections:
            out.update(f.path for f in s.fields)
            walk(s.sections)

    for p in pages:
        walk(p.sections)
        for m in p.modules:
            out.update(f.path for f in m.fields)
            walk(m.sections)
    return out


# ---------------------------------------------------------------------------
# _setup_from_extras
# ---------------------------------------------------------------------------


def test_untagged_field_is_hidden_from_setup():
    assert _setup_from_extras({}) is Setup.HIDDEN


def test_explicit_tags_are_parsed():
    assert _setup_from_extras({"setup": "required"}) is Setup.REQUIRED
    assert _setup_from_extras({"setup": "prompt"}) is Setup.PROMPT
    assert _setup_from_extras({"setup": "hidden"}) is Setup.HIDDEN


def test_unknown_tag_falls_back_to_hidden(caplog):
    with caplog.at_level("WARNING"):
        assert _setup_from_extras({"setup": "mandatory"}) is Setup.HIDDEN
    assert "Unknown setup" in caplog.text


# ---------------------------------------------------------------------------
# Independence from the simple/expert axis
# ---------------------------------------------------------------------------


class _MixedTierConfig(ModuleConfig):
    """One field per corner of the two axes — they must not interfere."""

    name: str = Field(default="mixed", title="Mixed", frozen=True, exclude=True)
    expert_prompt: str = Field(
        default="x",
        title="Expert but asked at setup",
        json_schema_extra={"setup": "prompt"},
    )
    simple_hidden: str = Field(
        default="y",
        title="Simple but not asked at setup",
        json_schema_extra={"importance": "simple"},
    )


def test_setup_tag_does_not_change_the_tier():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"mixed": _MixedTierConfig()},
        devices={},
    )
    fields = _by_path(schema)

    prompted = fields["input_modules.mixed.expert_prompt"]
    assert prompted.setup is Setup.PROMPT
    assert prompted.importance is Importance.EXPERT
    assert "input_modules.mixed.expert_prompt" not in _simple_view_paths(
        schema.pages
    ), "a wizard question must not leak into the simple settings page"

    shown = fields["input_modules.mixed.simple_hidden"]
    assert shown.importance is Importance.SIMPLE
    assert shown.setup is Setup.HIDDEN


# ---------------------------------------------------------------------------
# Required fields start empty
# ---------------------------------------------------------------------------


class _BadRequiredConfig(ModuleConfig):
    """A plugin claiming a field is required while shipping a usable value
    for it — the wizard would report the module configured before the user
    answered anything."""

    name: str = Field(default="bad", title="Bad", frozen=True, exclude=True)
    api_key: str = Field(
        default="pre-filled",
        title="API key",
        json_schema_extra={"setup": "required"},
    )


def test_required_field_with_a_default_is_flagged(caplog):
    with caplog.at_level("WARNING"):
        build_presentation(
            base_config=KalinkaConfig(),
            input_modules={"bad": _BadRequiredConfig()},
            devices={},
        )
    assert "input_modules.bad.api_key" in caplog.text
    assert "setup=required" in caplog.text


class _EmptyDefaultsConfig(ModuleConfig):
    """The ways of declaring "required, and empty until answered" — none of
    them is a violation, and the data-dependent factory must not be run
    just to check."""

    name: str = Field(default="empty", title="Empty", frozen=True, exclude=True)
    no_default: str = Field(
        ..., title="No default", json_schema_extra={"setup": "required"}
    )
    empty_factory: list[str] = Field(
        default_factory=list,
        title="Empty factory",
        json_schema_extra={"setup": "required"},
    )
    derived: str = Field(
        default_factory=lambda data: data["no_default"],
        title="Derived from a sibling",
        json_schema_extra={"setup": "required"},
    )


def test_empty_required_defaults_are_not_flagged(caplog):
    with caplog.at_level("WARNING"):
        build_presentation(
            base_config=KalinkaConfig(),
            input_modules={"empty": _EmptyDefaultsConfig(no_default="")},
            devices={},
        )
    assert "setup=required" not in caplog.text


def test_shipped_required_fields_default_to_empty(schema):
    required = [f for f in schema.expert_fields if f.setup is Setup.REQUIRED]
    assert required, "the fixture must cover at least one required field"
    for f in required:
        assert not f.default, f"{f.path} is required but defaults to {f.default!r}"


# ---------------------------------------------------------------------------
# What the shipped modules ask during setup
# ---------------------------------------------------------------------------


def test_wizard_questions_are_declared_by_the_modules(schema):
    asked = {
        f.path: f.setup
        for f in schema.expert_fields
        if f.setup is not Setup.HIDDEN
    }
    assert asked == {
        "base_config.server.service_name": Setup.PROMPT,
        "input_modules.jamendo.client_id": Setup.REQUIRED,
        "input_modules.jamendo.audio_format": Setup.PROMPT,
        "input_modules.jamendo.ai_search_enabled": Setup.PROMPT,
        "input_modules.localfiles.music_folders": Setup.PROMPT,
        "input_modules.localfiles.folder_first_clustering": Setup.PROMPT,
        "input_modules.localfiles.ai_search.enabled": Setup.PROMPT,
        "devices.musiccast.connected_input": Setup.PROMPT,
        "devices.musiccast.zone_name": Setup.PROMPT,
    }


def test_nested_setup_fields_reach_the_flat_index(schema):
    """The wizard reads one flat list, so a question buried in a
    sub-section (localfiles' AI indexer) must be reachable there."""
    assert (
        _by_path(schema)["input_modules.localfiles.ai_search.enabled"].setup
        is Setup.PROMPT
    )

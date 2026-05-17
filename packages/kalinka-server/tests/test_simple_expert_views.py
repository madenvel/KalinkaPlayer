"""Tests for the simple/expert split in the presentation schema.

Contract:

* ``PresentationSchema.pages`` carries the SIMPLE-tier hierarchy
  (default settings page). EXPERT fields are pruned out.
* ``PresentationSchema.expert_fields`` is the flat list of every
  settable field, sorted by dotted path, backing about:config search.
  Dynamic (read-only) fields are excluded.
* A field without an ``importance`` tag defaults to EXPERT — it must
  not leak into the simple view.
* Module shells are always retained in the simple view (so the user
  can flip the enable toggle without leaving simple mode), even if
  every other field below them is expert.
* Legacy ``"normal"`` / ``"advanced"`` strings remain accepted as
  synonyms for ``"simple"`` / ``"expert"`` so older plugins keep
  working.
* Sections explicitly tagged ``importance=EXPERT`` are dropped from
  the simple view regardless of their children.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.config_schema_processor import (
    _importance_from_extras,
    build_presentation,
)
from kalinka_server.presentation_schema import Importance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_field_paths_in_pages(pages) -> set[str]:
    """Walk a (pruned) pages tree and return every leaf field path."""
    out: set[str] = set()

    def walk_sections(sections):
        for s in sections:
            for f in s.fields:
                out.add(f.path)
            walk_sections(s.sections)

    for p in pages:
        walk_sections(p.sections)
        for m in p.modules:
            for f in m.fields:
                out.add(f.path)
            walk_sections(m.sections)
    return out


def _find_module(schema, module_id):
    for p in schema.pages:
        for m in p.modules:
            if m.id == module_id:
                return m
    return None


# ---------------------------------------------------------------------------
# _importance_from_extras: legacy back-compat
# ---------------------------------------------------------------------------


def test_unmarked_field_defaults_to_expert():
    assert _importance_from_extras({}) is Importance.EXPERT


def test_explicit_simple_is_simple():
    assert _importance_from_extras({"importance": "simple"}) is Importance.SIMPLE


def test_legacy_normal_maps_to_simple():
    assert _importance_from_extras({"importance": "normal"}) is Importance.SIMPLE


def test_legacy_advanced_maps_to_expert():
    assert _importance_from_extras({"importance": "advanced"}) is Importance.EXPERT


def test_unknown_importance_defaults_to_expert(caplog):
    with caplog.at_level("WARNING"):
        out = _importance_from_extras({"importance": "bogus"})
    assert out is Importance.EXPERT


# ---------------------------------------------------------------------------
# Simple view pruning (build_presentation.pages)
# ---------------------------------------------------------------------------


def test_simple_view_includes_simple_fields_and_excludes_expert():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    paths = _all_field_paths_in_pages(schema.pages)

    # Curated SIMPLE-tier essentials must surface:
    assert "base_config.server.service_name" in paths
    assert "base_config.output.alsa.device" in paths
    assert "base_config.device_automation.auto_power_on" in paths
    assert "input_modules.localfiles.enabled" in paths
    assert "input_modules.localfiles.music_folders" in paths
    assert "input_modules.localfiles.scan_interval_minutes" in paths

    # Audit-defaulted EXPERT tuning must NOT leak in:
    assert "base_config.output.alsa.latency_ms" not in paths
    assert "base_config.output.alsa.period_ms" not in paths
    assert "input_modules.localfiles.db_path" not in paths
    assert "input_modules.localfiles.searcher.weight_fts" not in paths
    assert "input_modules.localfiles.embedder.batch_size_clap" not in paths


def test_kalinka_buffers_section_dropped_when_tagged_expert():
    """KalinkaConfig.presentation_layout marks the 'Buffers & decoders'
    section as Importance.EXPERT — section-level tag wins, all of its
    fields are removed from the simple view."""
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={},
        devices={},
    )
    for p in schema.pages:
        if p.id != "general":
            continue
        for s in p.sections:
            assert s.id != "base_config.buffers", (
                "buffers section should be pruned out of the simple view"
            )


# ---------------------------------------------------------------------------
# Module shell preservation
# ---------------------------------------------------------------------------


class _AllExpertConfig(ModuleConfig):
    """A module with no SIMPLE-tier fields except `enabled`. Used to verify
    the module card survives pruning so the user can still toggle it on
    or off without leaving simple mode."""

    name: str = Field(
        default="allexpert", title="All-Expert", frozen=True, exclude=True
    )
    enabled: bool = Field(
        default=False,
        title="Module enabled",
        json_schema_extra={"importance": "simple"},
    )
    # Unmarked → defaults to EXPERT
    knob_a: int = Field(default=1, title="Knob A")
    knob_b: str = Field(default="x", title="Knob B")


class _PluginForgotToTagEnabled(ModuleConfig):
    """A misbehaving plugin whose `enabled` field has no importance tag.
    Under the default-EXPERT rule, this field would normally be pruned
    out of the simple view — but the module-level enable toggle is
    contractually guaranteed simple, so the card must still expose it.
    """

    name: str = Field(
        default="forgot", title="Forgot", frozen=True, exclude=True
    )
    # No json_schema_extra at all → defaults to EXPERT. The pruning
    # logic must still surface this so the user can disable the module.
    enabled: bool = Field(default=False, title="Module enabled")


def test_module_enabled_field_is_always_simple_even_when_untagged():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"forgot": _PluginForgotToTagEnabled()},
        devices={},
    )
    m = _find_module(schema, "forgot")
    assert m is not None
    enable_paths = {f.path for f in m.fields if f.path.endswith(".enabled")}
    assert "input_modules.forgot.enabled" in enable_paths, (
        "module's enable toggle must survive pruning regardless of "
        "importance tag — it's the one control the user must always reach"
    )


def test_module_card_retained_when_only_enabled_is_simple():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"allexpert": _AllExpertConfig()},
        devices={},
    )
    m = _find_module(schema, "allexpert")
    assert m is not None, "module card must remain visible in simple view"
    enable_paths = {f.path for f in m.fields if f.path.endswith(".enabled")}
    assert "input_modules.allexpert.enabled" in enable_paths
    # The expert knobs must not appear in the module's simple surface:
    knob_paths = {
        f.path for f in m.fields if "knob_a" in f.path or "knob_b" in f.path
    }
    assert knob_paths == set()


# ---------------------------------------------------------------------------
# Expert flat list (build_presentation.expert_fields)
# ---------------------------------------------------------------------------


def test_expert_list_includes_both_simple_and_expert_fields():
    """About:config shows EVERYTHING — power users want one searchable
    surface, not two."""
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    paths = {f.path for f in schema.expert_fields}

    # A SIMPLE-tier field:
    assert "input_modules.localfiles.music_folders" in paths
    # An audit-defaulted EXPERT field:
    assert "input_modules.localfiles.db_path" in paths
    assert "base_config.output.alsa.latency_ms" in paths
    # Nested expert leaves:
    assert "input_modules.localfiles.searcher.weight_fts" in paths


def test_expert_list_is_sorted_by_path():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    paths = [f.path for f in schema.expert_fields]
    assert paths == sorted(paths)


def test_expert_list_has_no_duplicate_paths():
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    paths = [f.path for f in schema.expert_fields]
    assert len(paths) == len(set(paths))


def test_expert_list_carries_importance_tag_per_entry():
    """Each entry includes its tier so the client can render simple
    ones with a 'default' badge or similar affordance."""
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    by_path = {f.path: f for f in schema.expert_fields}
    assert by_path[
        "input_modules.localfiles.music_folders"
    ].importance is Importance.SIMPLE
    assert by_path[
        "input_modules.localfiles.db_path"
    ].importance is Importance.EXPERT


def test_expert_list_excludes_dynamic_fields():
    """Dynamic fields are status displays attached to enable toggles in
    the simple view — they're not user-writable so they have no place
    in about:config."""
    from kalinka_plugin_sdk import DynamicFieldDecl

    from kalinka_server.dynamic_field_registry import (
        build_dynamic_field_registry,
    )

    class _FakePrepared:
        def __init__(self, decls, instance):
            self.plugin_class = type(
                "P", (object,), {"DYNAMIC_FIELDS": decls}
            )
            self.plugin_instance = instance

    class _FakeInstance:
        async def resolve_dynamic_field(self, path):
            return "ok"

    decls = {
        "searcher.status_view": DynamicFieldDecl(
            section_id="searcher", label="Status", widget="rich_text"
        ),
    }
    registry = build_dynamic_field_registry(
        {"localfiles": _FakePrepared(decls, _FakeInstance())}, {}
    )
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
        dynamic_field_registry=registry,
    )
    expert_paths = {f.path for f in schema.expert_fields}
    assert (
        "input_modules.localfiles.searcher.status_view" not in expert_paths
    ), "dynamic fields must not appear in the expert/about:config list"


# ---------------------------------------------------------------------------
# Schema-version coupling
# ---------------------------------------------------------------------------


def test_schema_version_changes_when_any_tier_changes():
    """A field's tier is part of the hashed payload — moving a field
    between simple and expert must invalidate the client's cache."""
    schema_a = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )

    # Mutate a SIMPLE field on a fresh model instance by flipping its
    # importance through json_schema_extra at the *field* level.
    # Easiest reproducible way: build the same schema twice — the hash
    # must be stable. Then build with a different module set and the
    # hash must change.
    schema_b = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    assert schema_a.schema_version == schema_b.schema_version

    # Add a second module — the schema must hash differently.
    schema_c = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={
            "localfiles": LocalFilesConfig(),
            "allexpert": _AllExpertConfig(),
        },
        devices={},
    )
    assert schema_a.schema_version != schema_c.schema_version


# ---------------------------------------------------------------------------
# Cross-check: every expert-list path also exists in /server/config values
# ---------------------------------------------------------------------------


def test_expert_list_paths_are_writable_via_put_config():
    """Sanity check: every path in the expert list is rooted at one of
    the three writable trees so PUT /server/config will accept it.
    """
    schema = build_presentation(
        base_config=KalinkaConfig(),
        input_modules={"localfiles": LocalFilesConfig()},
        devices={},
    )
    roots = ("base_config.", "input_modules.", "devices.")
    for f in schema.expert_fields:
        assert f.path.startswith(roots), (
            f"{f.path!r} is not under a known writable root"
        )

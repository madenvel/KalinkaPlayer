"""Tests for the module-level description carried by the presentation schema.

Contract:

* A module's display name and its one-line summary both come from the
  excluded ``name`` field — ``title`` and ``description``. That field is
  never rendered, so both are read out of band.
* The description is static, unlike the ``preview_fields`` subtitle which
  is composed from live values. It therefore reads the same for a module
  the user has not enabled, which is when it matters most.
* It works the same for input modules and output devices, since both are
  built by the same emitter.
"""

from __future__ import annotations

import pytest
from pydantic import Field

from kalinka_plugin_jamendo.config_model import JamendoConfig
from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_server.config_model import KalinkaConfig
from kalinka_server.config_schema_processor import build_presentation


def _build(input_modules=None, devices=None):
    return build_presentation(
        base_config=KalinkaConfig(),
        input_modules=input_modules or {},
        devices=devices or {},
    )


def _module(schema, module_id):
    for page in schema.pages:
        for m in page.modules:
            if m.id == module_id:
                return m
    pytest.fail(f"module {module_id} not in schema")


def test_description_comes_from_the_name_field():
    schema = _build(input_modules={"jamendo": JamendoConfig()})
    declared = JamendoConfig.model_fields["name"].description
    assert declared
    assert _module(schema, "jamendo").description == declared


def test_devices_carry_a_description_too():
    """Same emitter for both kinds — ``kind`` is only a parameter, so a
    device is not a second mechanism."""
    schema = _build(devices={"musiccast": KalinkaPluginMusiccastConfig()})
    module = _module(schema, "musiccast")
    assert module.kind == "device"
    assert module.description


def test_shipped_modules_all_describe_themselves():
    schema = _build(
        input_modules={
            "localfiles": LocalFilesConfig(),
            "jamendo": JamendoConfig(),
        },
        devices={"musiccast": KalinkaPluginMusiccastConfig()},
    )
    for module_id in ("localfiles", "jamendo", "musiccast"):
        description = _module(schema, module_id).description
        assert description, f"{module_id} has no description"
        assert description.strip().endswith("."), module_id


def test_description_is_optional():
    """A plugin that declares no description still builds — the field is
    absent, not empty."""

    class _Bare(ModuleConfig):
        name: str = Field(
            default="bare", title="Bare", frozen=True, exclude=True
        )

    assert _module(_build(input_modules={"bare": _Bare()}), "bare").description is None


def test_description_is_not_rendered_as_a_settable_field():
    """It rides on an excluded field, so it must not leak into the
    settings surface as an editable row."""
    schema = _build(input_modules={"jamendo": JamendoConfig()})
    paths = {f.path for f in schema.expert_fields}
    assert "input_modules.jamendo.name" not in paths

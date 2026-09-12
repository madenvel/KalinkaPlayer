#!/usr/bin/env python3
"""AcoustID with a key but no ``fpcalc`` binary.

The plugin used to load on the key alone and discover the missing binary once
per track, which on an indexing pass is one ERROR line per file in the library
and a FAILED row behind each one. A missing binary is a property of the
installation, so it is decided once and the chain skips the plugin.
"""

import asyncio
from unittest.mock import MagicMock, Mock, patch

from kalinka_plugin_localfiles import module_setup
from kalinka_plugin_localfiles.config_model import LocalFilesConfig
from kalinka_plugin_localfiles.enricher.acoustid_plugin import AcoustIdPlugin
from kalinka_plugin_sdk.module_health import ModuleHealthState

FPCALC = "/usr/bin/fpcalc"


def _section_id() -> str:
    decl = module_setup.KalinkaPluginLocalFiles.DYNAMIC_FIELDS["acoustid.status_view"]
    return decl.section_id


async def _noop(*args, **kwargs):
    return None


async def _empty_roots(*args, **kwargs):
    return []


def _plugin(tmp_path, *, api_key, fpcalc):
    config = LocalFilesConfig(
        music_folders=[str(tmp_path)],
        db_path=str(tmp_path / "localfiles.db"),
        artwork_path=str(tmp_path / "artwork"),
    )
    config.enricher.plugins.acoustid.api_key = api_key
    with patch(
        "kalinka_plugin_localfiles.enricher.acoustid_plugin.shutil.which",
        return_value=fpcalc,
    ):
        return AcoustIdPlugin(config, db_manager=MagicMock())


class TestTheChainSkipsAPluginThatCannotRun:
    def test_a_key_and_the_binary_can_enrich(self, tmp_path):
        plugin = _plugin(tmp_path, api_key="secret", fpcalc=FPCALC)
        assert plugin.can_enrich_track() is True

    def test_a_key_without_the_binary_cannot(self, tmp_path):
        """The regression this file exists for: one error, not one per track."""
        plugin = _plugin(tmp_path, api_key="secret", fpcalc=None)
        assert plugin.can_enrich_track() is False

    def test_the_binary_without_a_key_still_cannot(self, tmp_path):
        plugin = _plugin(tmp_path, api_key="", fpcalc=FPCALC)
        assert plugin.can_enrich_track() is False

    def test_the_missing_binary_is_reported_once_at_construction(
        self, tmp_path, caplog
    ):
        with caplog.at_level("ERROR"):
            _plugin(tmp_path, api_key="secret", fpcalc=None)
        assert sum("fpcalc" in r.message for r in caplog.records) == 1

    def test_no_key_does_not_complain_about_the_binary(self, tmp_path, caplog):
        """Nobody asked for fingerprinting, so nothing is missing."""
        with caplog.at_level("ERROR"):
            _plugin(tmp_path, api_key="", fpcalc=None)
        assert not any("fpcalc" in r.message for r in caplog.records)


class TestInstallingChromaprintReopensTheRows:
    """The plugin stays loaded on the key alone, so its signature is the only
    thing that can tell the enricher the install changed — see
    ``MetadataEnricher.compute_fingerprint``."""

    def test_the_signature_moves_when_the_binary_appears(self, tmp_path):
        without = _plugin(tmp_path, api_key="secret", fpcalc=None)
        with_it = _plugin(tmp_path, api_key="secret", fpcalc=FPCALC)
        assert without.config_signature() != with_it.config_signature()

    def test_the_signature_keeps_no_secret(self, tmp_path):
        signature = _plugin(
            tmp_path, api_key="secret", fpcalc=FPCALC
        ).config_signature()
        assert "secret" not in str(signature)
        assert signature == {"api_key_present": True, "fpcalc_present": True}


class TestTheSettingsPageSaysWhy:
    """The user-visible surface: the Status field of the AcoustID section.

    It sits beside the API key rather than on the enricher, because a key
    set against a missing binary is exactly what looks fine from there. A
    config change needs a restart to take effect, so the startup snapshot
    the field renders always matches the config actually running.
    """

    def _evaluated(self, tmp_path, *, api_key, fpcalc, enricher=True):
        config = LocalFilesConfig(
            music_folders=[str(tmp_path)],
            db_path=str(tmp_path / "localfiles.db"),
            artwork_path=str(tmp_path / "artwork"),
        )
        config.enricher.enabled = enricher
        config.enricher.plugins.acoustid.api_key = api_key
        plugin = module_setup.KalinkaPluginLocalFiles()
        plugin._librarian_proc = Mock(is_alive=Mock(return_value=True))
        with patch.object(
            module_setup.shutil, "which", return_value=fpcalc
        ), patch.object(module_setup.asyncio, "sleep", _noop):
            asyncio.run(plugin._evaluate_subfeatures(config))
        return plugin

    def _status_view(self, tmp_path, field="acoustid.status_view", **kwargs):
        plugin = self._evaluated(tmp_path, **kwargs)
        with patch.object(
            module_setup.shutil, "which", return_value=kwargs["fpcalc"]
        ):
            return asyncio.run(plugin.resolve_dynamic_field(field))

    def _module_state(self, tmp_path, **kwargs):
        plugin = self._evaluated(tmp_path, **kwargs)
        with patch.object(plugin, "_music_folder_statuses", _empty_roots):
            return asyncio.run(plugin.get_state())

    def test_the_field_is_declared_on_the_acoustid_section(self):
        """Beside the key, not one section out on the enricher."""
        assert _section_id() == "enricher.plugins.acoustid"

    def test_the_declared_section_is_a_real_one(self):
        """A section_id matching nothing is dropped with only a log line —
        the field just silently never appears. Walk the config model the
        schema is generated from, so a renamed field fails here instead."""
        model = LocalFilesConfig
        for part in _section_id().split("."):
            model = model.model_fields[part].annotation
        assert "api_key" in model.model_fields

    def test_a_configured_key_with_no_binary_warns(self, tmp_path):
        state = self._evaluated(tmp_path, api_key="secret", fpcalc=None)._subfeatures[
            "acoustid"
        ]
        assert state.state is ModuleHealthState.WARNING
        assert state.missing_packages == ()

    def test_the_status_field_names_the_package_to_install(self, tmp_path):
        """What the user reads in Settings, rather than the state behind it."""
        status = self._status_view(tmp_path, api_key="secret", fpcalc=None)
        assert status.startswith("**Warning**")
        assert "fpcalc" in status and "libchromaprint-tools" in status

    def test_the_status_field_is_quiet_when_nothing_is_wrong(self, tmp_path):
        status = self._status_view(tmp_path, api_key="secret", fpcalc=FPCALC)
        assert status.startswith("**Ready**")
        assert "fpcalc" not in status

    def test_no_key_reads_as_off_rather_than_broken(self, tmp_path):
        status = self._status_view(tmp_path, api_key="", fpcalc=None)
        assert status.startswith("**Disabled**")
        assert "fpcalc" not in status

    def test_a_disabled_enricher_does_not_blame_the_key(self, tmp_path):
        status = self._status_view(
            tmp_path, api_key="secret", fpcalc=None, enricher=False
        )
        assert status.startswith("**Disabled**")
        assert "API key" not in status

    def test_the_enricher_status_stays_about_the_enricher(self, tmp_path):
        status = self._status_view(
            tmp_path, field="enricher.status_view", api_key="secret", fpcalc=None
        )
        assert status.startswith("**Ready**")

    def test_the_roll_up_names_acoustid_not_the_enricher(self, tmp_path):
        """The module badge only summarises; the section carries the fix."""
        module_state = self._module_state(tmp_path, api_key="secret", fpcalc=None)
        assert module_state.state is ModuleHealthState.WARNING
        assert "AcoustID" in module_state.message
        assert module_state.missing_packages == []


async def _empty_roots(*args, **kwargs):
    return []

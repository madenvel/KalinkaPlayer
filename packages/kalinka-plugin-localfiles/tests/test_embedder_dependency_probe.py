from unittest.mock import Mock

import kalinka_plugin_localfiles.pip_utils as pip_utils
import kalinka_plugin_localfiles.embedder.embedder as embedder


def test_ensure_package_resolves_known_pip_spec(monkeypatch):
    """Embedder knows how to resolve laion_clap → laion-clap."""
    pip_utils._install_failed.clear()

    def fake_find_spec(name: str):
        return object() if name == "laion_clap" else None

    monkeypatch.setattr(pip_utils.importlib.util, "find_spec", fake_find_spec)

    run_mock = Mock()
    monkeypatch.setattr(pip_utils.subprocess, "run", run_mock)

    assert embedder._ensure_package("laion_clap") is True
    run_mock.assert_not_called()


def test_resolve_probe_name_passthrough():
    """With no aliases, probe name equals import name."""
    assert pip_utils.resolve_probe_name("laion_clap", {}) == "laion_clap"
    assert pip_utils.resolve_probe_name("numpy", {}) == "numpy"


def test_resolve_probe_name_with_alias():
    """Aliases redirect the probe name."""
    aliases = {"essentia_tensorflow": "essentia"}
    assert pip_utils.resolve_probe_name("essentia_tensorflow", aliases) == "essentia"
    assert pip_utils.resolve_probe_name("numpy", aliases) == "numpy"

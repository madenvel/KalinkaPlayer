from unittest.mock import Mock

import kalinka_plugin_localfiles.embedder.embedder as embedder


def test_ensure_package_resolves_known_pip_spec(monkeypatch):
    """Embedder knows how to resolve laion_clap → laion-clap."""
    embedder._install_failed.clear()

    def fake_find_spec(name: str):
        return object() if name == "laion_clap" else None

    monkeypatch.setattr(embedder.importlib.util, "find_spec", fake_find_spec)

    run_mock = Mock()
    monkeypatch.setattr(embedder.subprocess, "run", run_mock)

    assert embedder._ensure_package("laion_clap") is True
    run_mock.assert_not_called()


def test_resolve_probe_name_passthrough():
    """With no aliases, probe name equals import name."""
    assert embedder._resolve_probe_name("laion_clap") == "laion_clap"
    assert embedder._resolve_probe_name("numpy") == "numpy"

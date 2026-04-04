from unittest.mock import Mock

import kalinka_plugin_localfiles.embedder.embedder as embedder


def test_ensure_package_resolves_essentia_tensorflow_alias(monkeypatch):
    embedder._install_failed.clear()

    def fake_find_spec(name: str):
        # Simulate environment where only the real module name is importable.
        return object() if name == "essentia" else None

    monkeypatch.setattr(embedder.importlib.util, "find_spec", fake_find_spec)

    run_mock = Mock()
    monkeypatch.setattr(embedder.subprocess, "run", run_mock)

    assert embedder._ensure_package("essentia_tensorflow") is True
    run_mock.assert_not_called()


def test_resolve_probe_name_for_essentia_tensorflow():
    assert embedder._resolve_probe_name("essentia_tensorflow") == "essentia"
    assert embedder._resolve_probe_name("essentia") == "essentia"

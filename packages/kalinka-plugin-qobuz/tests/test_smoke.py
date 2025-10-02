"""
Pytest smoke tests for kalinka-plugin-kalinka-plugin-qobuz
"""

import pytest
from importlib.metadata import entry_points


@pytest.mark.smoke
def test_entry_point_visible():
    """Test that the plugin entry point is discoverable"""
    eps = entry_points(group="kalinka.plugins")
    # In editable mode, ensure it's discoverable after `pip install -e .`
    assert any(
        ep.name == "kalinka_plugin_qobuz" for ep in eps
    ), "Plugin entry point 'kalinka_plugin_qobuz' not found in kalinka.plugins group"

    for ep in eps:
        if ep.name == "kalinka_plugin_qobuz":
            plugin = ep.load()
            assert plugin is not None
            assert hasattr(plugin, "PLUGIN_ID")
            assert plugin.PLUGIN_ID == "qobuz"
            assert hasattr(plugin, "REQUIRES_SDK")
            assert hasattr(plugin, "CONFIG_MODEL")
            obj = plugin()
            assert hasattr(obj, "setup")
            assert hasattr(obj, "shutdown")

            config = plugin.CONFIG_MODEL()
            assert config is not None

            break

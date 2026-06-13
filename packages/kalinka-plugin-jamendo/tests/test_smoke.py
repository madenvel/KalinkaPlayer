"""
Pytest smoke tests for kalinka-plugin-jamendo
"""

import pytest
from importlib.metadata import entry_points


@pytest.mark.smoke
def test_entry_point_visible():
    """The plugin entry point is discoverable and well-formed."""
    eps = entry_points(group="kalinka.plugins")
    assert any(
        ep.name == "kalinka_plugin_jamendo" for ep in eps
    ), "Plugin entry point 'kalinka_plugin_jamendo' not found in kalinka.plugins group"

    for ep in eps:
        if ep.name == "kalinka_plugin_jamendo":
            plugin = ep.load()
            assert plugin is not None
            assert hasattr(plugin, "PLUGIN_ID")
            assert plugin.PLUGIN_ID == "jamendo"
            assert hasattr(plugin, "REQUIRES_SDK")
            assert hasattr(plugin, "CONFIG_MODEL")
            obj = plugin()
            assert hasattr(obj, "setup")
            assert hasattr(obj, "shutdown")

            config = plugin.CONFIG_MODEL()
            assert config is not None
            break

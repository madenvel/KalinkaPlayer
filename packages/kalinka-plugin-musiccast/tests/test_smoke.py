"""
Pytest smoke tests for kalinka-plugin-kalinka-plugin-musiccast
"""

import pytest
from importlib.metadata import entry_points


@pytest.mark.smoke
def test_entry_point_visible():
    """Test that the plugin entry point is discoverable"""
    eps = entry_points(group="kalinka.plugins")
    # In editable mode, ensure it's discoverable after `pip install -e .`
    assert any(
        ep.name == "kalinka_plugin_musiccast" for ep in eps
    ), "Plugin entry point 'kalinka_plugin_musiccast' not found in kalinka.plugins group"


@pytest.mark.smoke
def test_imports():
    """Test that all plugin modules can be imported"""
    # These imports should not raise ImportError
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
    from kalinka_plugin_musiccast.module_setup import setup
    from kalinka_plugin_musiccast.musiccast import KalinkaPluginMusiccastDevice

    # Verify the imported classes/functions exist
    assert KalinkaPluginMusiccastConfig is not None
    assert setup is not None
    assert KalinkaPluginMusiccastDevice is not None


@pytest.mark.smoke
def test_config_model_instantiation():
    """Test that config model can be instantiated with correct defaults"""
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig

    config = KalinkaPluginMusiccastConfig()

    assert config.name == "musiccast"
    assert config.enabled is False


def test_config_model_validation():
    """Test that config model validation works"""
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig

    # Test with valid data
    config = KalinkaPluginMusiccastConfig(enabled=True)
    assert config.enabled is True
    assert config.name == "musiccast"

    # Test with custom name (frozen=True prevents modification after creation, not during init)
    config_with_name = KalinkaPluginMusiccastConfig(name="different_name", enabled=True)
    assert config_with_name.name == "different_name"
    assert config_with_name.enabled is True


def test_config_model_frozen_field():
    """Test that name field is frozen after creation"""
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
    import pytest

    config = KalinkaPluginMusiccastConfig()

    # Attempting to modify the frozen 'name' field should raise an exception
    with pytest.raises(Exception):  # Should raise ValidationError or similar
        config.name = "modified_name"


@pytest.mark.smoke
def test_device_instantiation():
    """Test that device can be instantiated"""
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
    from kalinka_plugin_musiccast.musiccast import KalinkaPluginMusiccastDevice

    config = KalinkaPluginMusiccastConfig()
    device = KalinkaPluginMusiccastDevice(config, None, None)

    assert device is not None


def test_module_setup_constants():
    """Test that module setup has required constants"""
    from kalinka_plugin_musiccast.module_setup import (
        REQUIRES_SDK,
        PLUGIN_ID,
        Config,
        setup,
    )

    assert REQUIRES_SDK == ">=1.0,<2"
    assert PLUGIN_ID == "musiccast"
    assert Config is not None
    assert callable(setup)


@pytest.mark.parametrize(
    "method_name",
    [
        "get_volume",
        "set_volume",
        "power_on",
        "is_power_on",
        "power_off",
        "supported_functions",
    ],
)
def test_device_has_required_methods(method_name):
    """Test that device has all required interface methods"""
    from kalinka_plugin_musiccast.config_model import KalinkaPluginMusiccastConfig
    from kalinka_plugin_musiccast.musiccast import KalinkaPluginMusiccastDevice

    config = KalinkaPluginMusiccastConfig()
    device = KalinkaPluginMusiccastDevice(config, None, None)

    assert hasattr(device, method_name), f"Missing required method: {method_name}"
    assert callable(
        getattr(device, method_name)
    ), f"Method {method_name} is not callable"

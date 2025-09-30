"""
Pytest smoke tests for kalinka-plugin-kalinka-plugin-dummydevice
"""

import pytest
from importlib.metadata import entry_points


@pytest.mark.smoke
def test_entry_point_visible():
    """Test that the plugin entry point is discoverable"""
    eps = entry_points(group="kalinka.plugins")
    # In editable mode, ensure it's discoverable after `pip install -e .`
    assert any(
        ep.name == "kalinka_plugin_dummydevice" for ep in eps
    ), "Plugin entry point 'kalinka_plugin_dummydevice' not found in kalinka.plugins group"


@pytest.mark.smoke
def test_imports():
    """Test that all plugin modules can be imported"""
    # These imports should not raise ImportError
    from kalinka_plugin_dummydevice.config_model import DummydeviceConfig
    from kalinka_plugin_dummydevice.module_setup import setup
    from kalinka_plugin_dummydevice.dummydevice import DummyDevice

    # Verify the imported classes/functions exist
    assert DummydeviceConfig is not None
    assert setup is not None
    assert DummyDevice is not None


@pytest.mark.smoke
def test_config_model_instantiation():
    """Test that config model can be instantiated with correct defaults"""
    from kalinka_plugin_dummydevice.config_model import DummydeviceConfig

    config = DummydeviceConfig()

    assert config.name == "dummydevice"
    assert config.enabled is False


def test_config_model_validation():
    """Test that config model validation works"""
    from kalinka_plugin_dummydevice.config_model import DummydeviceConfig

    # Test with valid data
    config = DummydeviceConfig(enabled=True)
    assert config.enabled is True
    assert config.name == "dummydevice"

    # Test with custom name (frozen=True prevents modification after creation, not during init)
    config_with_name = DummydeviceConfig(name="different_name", enabled=True)
    assert config_with_name.name == "different_name"
    assert config_with_name.enabled is True


def test_config_model_frozen_field():
    """Test that name field is frozen after creation"""
    from kalinka_plugin_dummydevice.config_model import DummydeviceConfig
    import pytest

    config = DummydeviceConfig()

    # Attempting to modify the frozen 'name' field should raise an exception
    with pytest.raises(Exception):  # Should raise ValidationError or similar
        config.name = "modified_name"


@pytest.mark.smoke
def test_device_instantiation():
    """Test that device can be instantiated"""
    from kalinka_plugin_dummydevice.config_model import DummydeviceConfig
    from kalinka_plugin_dummydevice.dummydevice import DummyDevice

    config = DummydeviceConfig()
    device = DummyDevice(config)

    assert device is not None
    assert config is not None


def test_module_setup_constants():
    """Test that module setup has required constants"""
    from kalinka_plugin_dummydevice.module_setup import (
        REQUIRES_SDK,
        PLUGIN_ID,
        Config,
        setup,
    )

    assert REQUIRES_SDK == ">=1.0,<2"
    assert PLUGIN_ID == "dummydevice"
    assert Config is not None
    assert callable(setup)

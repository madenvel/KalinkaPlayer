"""
Pytest smoke tests for kalinka-plugin-localfiles
"""

import pytest
from importlib.metadata import entry_points


@pytest.mark.smoke
def test_entry_point_visible():
    """Test that the plugin entry point is discoverable"""
    eps = entry_points(group="kalinka.plugins")
    # In editable mode, ensure it's discoverable after `pip install -e .`
    assert any(
        ep.name == "kalinka_plugin_localfiles" for ep in eps
    ), "Plugin entry point 'kalinka_plugin_localfiles' not found in kalinka.plugins group"


@pytest.mark.smoke
def test_imports():
    """Test that all plugin modules can be imported"""
    # These imports should not raise ImportError
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig
    from kalinka_plugin_localfiles.module_setup import setup
    from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule

    # Verify the imported classes/functions exist
    assert LocalFilesConfig is not None
    assert setup is not None
    assert LocalFilesInputModule is not None


@pytest.mark.smoke
def test_config_model_instantiation():
    """Test that config model can be instantiated with correct defaults"""
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig

    config = LocalFilesConfig()

    assert config.name == "localfiles"


def test_config_model_validation():
    """Test that config model validation works"""
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig

    # Test with valid data
    config = LocalFilesConfig(enabled=True)
    assert config.enabled is True
    assert config.name == "localfiles"

    # Test with custom name (frozen=True prevents modification after creation, not during init)
    config_with_name = LocalFilesConfig(name="different_name", enabled=True)
    assert config_with_name.name == "different_name"
    assert config_with_name.enabled is True


def test_config_model_frozen_field():
    """Test that name field is frozen after creation"""
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig
    import pytest

    config = LocalFilesConfig()

    # Attempting to modify the frozen 'name' field should raise an exception
    with pytest.raises(Exception):  # Should raise ValidationError or similar
        config.name = "modified_name"


@pytest.mark.smoke
def test_input_module_instantiation():
    """Test that input module can be instantiated"""
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig
    from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule

    config = LocalFilesConfig()
    module = LocalFilesInputModule(config, None, None)

    assert module is not None


@pytest.mark.smoke
def test_input_module_name():
    """Test that input module returns correct name"""
    from kalinka_plugin_localfiles.config_model import LocalFilesConfig
    from kalinka_plugin_localfiles.localfiles import LocalFilesInputModule

    config = LocalFilesConfig()
    module = LocalFilesInputModule(config, None, None)

    assert module.module_name() == "localfiles"


def test_module_setup_constants():
    """Test that module setup has required constants"""
    from kalinka_plugin_localfiles.module_setup import (
        REQUIRES_SDK,
        PLUGIN_ID,
        PLUGIN_TYPE,
        Config,
        setup,
    )

    assert REQUIRES_SDK == ">=1.0,<2"
    assert PLUGIN_ID == "localfiles"
    assert PLUGIN_TYPE == "input_module"
    assert Config is not None
    assert callable(setup)

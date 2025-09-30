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


@pytest.mark.smoke
def test_imports():
    """Test that all plugin modules can be imported"""
    # These imports should not raise ImportError
    from kalinka_plugin_qobuz.config_model import QobuzConfig
    from kalinka_plugin_qobuz.module_setup import setup
    from kalinka_plugin_qobuz.qobuz import (
        QobuzInputModule,
    )

    # Verify the imported classes/functions exist
    assert QobuzConfig is not None
    assert setup is not None
    assert QobuzInputModule is not None


@pytest.mark.smoke
def test_config_model_instantiation():
    """Test that config model can be instantiated with correct defaults"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig

    config = QobuzConfig()

    assert config.name == "qobuz"
    assert config.enabled is True


def test_config_model_validation():
    """Test that config model validation works"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig

    # Test with valid data
    config = QobuzConfig(enabled=True)
    assert config.enabled is True
    assert config.name == "qobuz"

    # Test with custom name (frozen=True prevents modification after creation, not during init)
    config_with_name = QobuzConfig(name="different_name", enabled=True)
    assert config_with_name.name == "different_name"
    assert config_with_name.enabled is True


def test_config_model_frozen_field():
    """Test that name field is frozen after creation"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig
    import pytest

    config = QobuzConfig()

    # Attempting to modify the frozen 'name' field should raise an exception
    with pytest.raises(Exception):  # Should raise ValidationError or similar
        config.name = "modified_name"


@pytest.mark.smoke
def test_input_module_instantiation():
    """Test that input module can be instantiated"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig
    from kalinka_plugin_qobuz.qobuz import (
        QobuzInputModule,
    )

    config = QobuzConfig()
    module = QobuzInputModule(config, None, None)

    assert config is not None
    assert module is not None


@pytest.mark.smoke
def test_input_module_name():
    """Test that input module returns correct name"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig
    from kalinka_plugin_qobuz.qobuz import (
        QobuzInputModule,
    )

    config = QobuzConfig()
    module = QobuzInputModule(config, None, None)

    assert module.module_name() == "Qobuz"


def test_module_setup_constants():
    """Test that module setup has required constants"""
    from kalinka_plugin_qobuz.module_setup import (
        REQUIRES_SDK,
        PLUGIN_ID,
        Config,
        setup,
    )

    assert REQUIRES_SDK == ">=1.0,<2"
    assert PLUGIN_ID == "qobuz"
    assert Config is not None
    assert callable(setup)


@pytest.mark.parametrize(
    "method_name",
    [
        "search",
        "browse",
        "get_track_info",
        "list_favorite",
        "get_favorite_ids",
        "add_to_favorite",
        "remove_from_favorite",
        "list_genre",
        "get",
        "playlist_user_list",
        "playlist_create",
        "playlist_update",
        "playlist_delete",
        "playlist_add_tracks",
        "playlist_remove_tracks",
        "get_resource_path",
    ],
)
def test_input_module_has_required_methods(method_name):
    """Test that input module has all required interface methods"""
    from kalinka_plugin_qobuz.config_model import QobuzConfig
    from kalinka_plugin_qobuz.qobuz import (
        QobuzInputModule,
    )

    config = QobuzConfig()
    module = QobuzInputModule(config, None, None)

    assert hasattr(module, method_name), f"Missing required method: {method_name}"
    assert callable(
        getattr(module, method_name)
    ), f"Method {method_name} is not callable"

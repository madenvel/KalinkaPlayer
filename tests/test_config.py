import pytest
import json
import yaml
import tempfile
import os
from src.config import Config


@pytest.fixture
def sample_schema():
    schema = {
        "type": "section",
        "name": "Root",
        "description": "Root configuration",
        "elements": {
            "server": {
                "type": "section",
                "name": "Server",
                "description": "Server configuration",
                "elements": {
                    "host": {
                        "type": "string",
                        "name": "Host",
                        "description": "Server host",
                        "default": "localhost",
                    },
                    "port": {
                        "type": "integer",
                        "name": "Port",
                        "description": "Server port",
                        "default": 8080,
                    },
                    "debug": {
                        "type": "boolean",
                        "name": "Debug",
                        "description": "Debug mode",
                        "default": False,
                    },
                },
            },
            "app": {
                "type": "section",
                "name": "Application",
                "description": "Application configuration",
                "elements": {
                    "name": {
                        "type": "string",
                        "name": "Name",
                        "description": "Application name",
                        "required": "yes",
                    },
                    "mode": {
                        "type": "enum",
                        "name": "Mode",
                        "description": "Application mode",
                        "values": ["development", "production", "testing"],
                        "default": "development",
                    },
                },
            },
        },
    }
    return json.dumps(schema)


@pytest.fixture
def sample_config():
    config = {"server": {"host": "0.0.0.0", "port": 9090}, "app": {"name": "TestApp"}}
    return yaml.dump(config)


@pytest.fixture
def config_instance(sample_config, sample_schema):
    return Config(sample_config, sample_schema, "test_location.yaml")


def test_init_config(config_instance):
    assert config_instance.location == "test_location.yaml"
    assert isinstance(config_instance.config_dict, dict)
    assert isinstance(config_instance.schema_dict, dict)


def test_getitem(config_instance):
    # Test explicit values
    assert config_instance["server.host"] == "0.0.0.0"
    assert config_instance["server.port"] == 9090
    assert config_instance["app.name"] == "TestApp"

    # Test default values
    assert config_instance["server.debug"] is False
    assert config_instance["app.mode"] == "development"

    # Test missing key
    with pytest.raises(KeyError):
        config_instance["nonexistent.key"]


def test_setitem(config_instance):
    # Set new values
    config_instance["server.host"] = "127.0.0.1"
    config_instance["server.debug"] = True
    config_instance["app.mode"] = "production"

    # Verify changes
    assert config_instance["server.host"] == "127.0.0.1"
    assert config_instance["server.debug"] is True
    assert config_instance["app.mode"] == "production"

    # Test type validation
    with pytest.raises(TypeError):
        config_instance["server.port"] = "not_an_integer"

    # Test enum validation
    with pytest.raises(ValueError):
        config_instance["app.mode"] = "invalid_mode"

    # Test setting nonexistent key
    with pytest.raises(KeyError):
        config_instance["nonexistent.key"] = "value"


def test_get_method(config_instance):
    # Test existing keys
    assert config_instance.get("server.host") == "0.0.0.0"

    # Test default return value
    assert config_instance.get("nonexistent.key") is None
    assert config_instance.get("nonexistent.key", "default_value") == "default_value"


def test_flatten_config(config_instance):
    flat_config = config_instance.flatten_config()
    assert flat_config["server.host"] == "0.0.0.0"
    assert flat_config["server.port"] == 9090
    assert flat_config["server.debug"] is False
    assert flat_config["app.name"] == "TestApp"
    assert flat_config["app.mode"] == "development"


def test_get_full_config(config_instance):
    full_config = config_instance.get_full_config()

    # Check structure and values
    assert full_config["type"] == "section"
    assert full_config["elements"]["server"]["elements"]["host"]["value"] == "0.0.0.0"
    assert full_config["elements"]["app"]["elements"]["name"]["value"] == "TestApp"

    # Check default values are included
    assert "default" in full_config["elements"]["server"]["elements"]["debug"]
    assert full_config["elements"]["server"]["elements"]["debug"]["default"] is False


def test_dump_and_save():
    schema = {
        "type": "section",
        "elements": {"test": {"type": "string", "default": "value"}},
    }
    config = {"test": "custom_value"}

    # Create temporary file for testing
    with tempfile.NamedTemporaryFile(delete=False) as temp:
        temp_path = temp.name

    try:
        # Initialize config and save
        config_obj = Config(yaml.dump(config), json.dumps(schema), temp_path)
        config_obj.save()

        # Read the saved file and verify
        with open(temp_path, "r") as f:
            saved_content = f.read()

        # Load the saved YAML and check its content
        saved_yaml = yaml.safe_load(saved_content)
        assert saved_yaml["test"] == "custom_value"

        # Test saving to a different location
        new_temp = tempfile.NamedTemporaryFile(delete=False).name
        try:
            config_obj.save(new_temp)
            with open(new_temp, "r") as f:
                assert "custom_value" in f.read()
        finally:
            if os.path.exists(new_temp):
                os.unlink(new_temp)

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_validation_on_init():
    # Invalid config (wrong type for port)
    invalid_config = {
        "server": {"host": "localhost", "port": "invalid_port_value"},
        "app": {"name": "TestApp"},
    }

    schema = {
        "type": "section",
        "elements": {
            "server": {
                "type": "section",
                "elements": {
                    "host": {"type": "string"},
                    "port": {"type": "integer", "description": "Port number"},
                },
            },
            "app": {
                "type": "section",
                "elements": {"name": {"type": "string", "required": "yes"}},
            },
        },
    }

    # Should raise TypeError due to invalid port type
    with pytest.raises(TypeError) as excinfo:
        Config(yaml.dump(invalid_config), json.dumps(schema), "test.yaml")

    assert "Expected integer" in str(excinfo.value)


def test_update_and_save_config():
    schema = {
        "type": "section",
        "elements": {
            "server": {
                "type": "section",
                "elements": {
                    "host": {"type": "string", "default": "localhost"},
                    "port": {"type": "integer", "default": 8080},
                },
            }
        },
    }
    config = {"server": {"host": "127.0.0.1", "port": 8000}}

    # Create temporary file for testing
    with tempfile.NamedTemporaryFile(delete=False) as temp:
        temp_path = temp.name

    try:
        # Initialize config
        config_obj = Config(yaml.dump(config), json.dumps(schema), temp_path)

        # Update a value
        config_obj["server.port"] = 9000

        # Save the updated config
        config_obj.save()

        # Read the saved file and verify
        with open(temp_path, "r") as f:
            saved_content = f.read()

        # Load the saved YAML and check its content
        saved_yaml = yaml.safe_load(saved_content)
        assert saved_yaml["server"]["port"] == 9000
        assert saved_yaml["server"]["host"] == "127.0.0.1"

        # Create new config instance from the saved file
        with open(temp_path, "r") as f:
            reloaded_config = Config(f.read(), json.dumps(schema), temp_path)

        # Verify the updated value persisted
        assert reloaded_config["server.port"] == 9000

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)

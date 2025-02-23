import pytest
import json
import yaml
from src.config import Config


@pytest.fixture
def mock_config():
    return yaml.safe_load(
        """
    section1:
      key1: value1
      key2: 10
      key3: true
    section2:
      key4: 3.14
      key5: option1
    """
    )


@pytest.fixture
def mock_schema():
    return json.loads(
        """
    {
      "type": "section",
      "elements": {
        "section1": {
          "type": "section",
          "elements": {
            "key1": {"type": "string", "description": "A string key", "required": "yes"},
            "key2": {"type": "integer", "description": "An integer key", "default": 5},
            "key3": {"type": "boolean", "description": "A boolean key", "default": false}
          }
        },
        "section2": {
          "type": "section",
          "elements": {
            "key4": {"type": "number", "description": "A number key", "required": "yes"},
            "key5": {"type": "enum", "description": "An enum key", "values": ["option1", "option2"], "default": "option2"}
          }
        }
      }
    }
    """
    )


def test_flatten_config(mock_config, mock_schema):
    config = Config(yaml.dump(mock_config), json.dumps(mock_schema))
    flattened = config.flatten_config()
    assert flattened == {
        "section1.key1": "value1",
        "section1.key2": 10,
        "section1.key3": True,
        "section2.key4": 3.14,
        "section2.key5": "option1",
    }


def test_getitem(mock_config, mock_schema):
    config = Config(yaml.dump(mock_config), json.dumps(mock_schema))
    assert config["section1.key1"] == "value1"
    assert config["section1.key2"] == 10
    assert config["section1.key3"] == True
    assert config["section2.key4"] == 3.14
    assert config["section2.key5"] == "option1"


def test_getitem_default(mock_config, mock_schema):
    del mock_config["section1"]["key2"]
    config = Config(yaml.dump(mock_config), json.dumps(mock_schema))
    assert config["section1.key2"] == 5


def test_get_full_config(mock_config, mock_schema):
    config = Config(yaml.dump(mock_config), json.dumps(mock_schema))
    full_config = config.get_full_config()
    assert full_config == {
        "section1": {
            "key1": {
                "description": "A string key",
                "type": "string",
                "value": "value1",
            },
            "key2": {
                "description": "An integer key",
                "type": "integer",
                "value": 10,
                "default": 5,
            },
            "key3": {
                "description": "A boolean key",
                "type": "boolean",
                "value": True,
                "default": False,
            },
        },
        "section2": {
            "key4": {"description": "A number key", "type": "number", "value": 3.14},
            "key5": {
                "description": "An enum key",
                "type": "enum",
                "value": "option1",
                "default": "option2",
            },
        },
    }


def test_validate_config_missing_required(mock_config, mock_schema):
    del mock_config["section1"]["key1"]
    with pytest.raises(
        ValueError, match="Missing required field: A string key at .section1.key1"
    ):
        Config(yaml.dump(mock_config), json.dumps(mock_schema))


def test_validate_config_invalid_type(mock_config, mock_schema):
    mock_config["section1"]["key2"] = "not_an_integer"
    with pytest.raises(
        TypeError,
        match="Expected integer for An integer key at .section1.key2, got str",
    ):
        Config(yaml.dump(mock_config), json.dumps(mock_schema))


def test_validate_config_invalid_enum(mock_config, mock_schema):
    mock_config["section2"]["key5"] = "invalid_option"
    with pytest.raises(
        ValueError,
        match="Invalid value for An enum key at .section2.key5, got invalid_option, values \['option1', 'option2'\]",
    ):
        Config(yaml.dump(mock_config), json.dumps(mock_schema))

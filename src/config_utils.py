"""Configuration utilities for converting between dict and Pydantic models."""

from typing import Dict, Any
import yaml
from .config_model import KalinkaConfig


def load_config_from_dict(config_dict: Dict[str, Any]) -> KalinkaConfig:
    """Load configuration from a dictionary and validate it with Pydantic."""
    return KalinkaConfig.model_validate(config_dict)


def load_config_from_yaml(yaml_path: str) -> KalinkaConfig:
    """Load configuration from a YAML file and validate it with Pydantic."""
    with open(yaml_path, "r") as f:
        config_dict = yaml.safe_load(f)
    return load_config_from_dict(config_dict)


def save_config_to_yaml(config: KalinkaConfig, yaml_path: str) -> None:
    """Save a Pydantic configuration model to a YAML file."""
    config_dict = config.model_dump(exclude_unset=True)
    with open(yaml_path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False, indent=2)


def get_config_schema() -> Dict[str, Any]:
    """Get the JSON schema for the configuration."""
    return KalinkaConfig.model_json_schema()


def validate_config_dict(config_dict: Dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a configuration dictionary against the Pydantic model.

    Returns:
        tuple: (is_valid, list_of_errors)
    """
    try:
        KalinkaConfig.model_validate(config_dict)
        return True, []
    except Exception as e:
        return False, [str(e)]


def create_default_config() -> KalinkaConfig:
    """Create a default configuration with minimal required fields."""
    from .config_model import (
        ServerConfig,
        OutputConfig,
        AlsaConfig,
        DecoderConfig,
    )

    return KalinkaConfig(
        server=ServerConfig(
            interface="0.0.0.0", port=8080, service_name="Kalinka Player"
        ),
        output=OutputConfig(alsa=AlsaConfig(device="default")),
        decoder=DecoderConfig(),
    )

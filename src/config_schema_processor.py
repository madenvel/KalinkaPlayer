"""Configuration schema processor for runtime type and validation info extraction."""

from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel
from pydantic.fields import FieldInfo
import logging

from src.base_config_model import ModuleConfig

logger = logging.getLogger(__name__.split(".")[-1])


def annotation_to_type(annotation: Any) -> str:
    """Convert a Pydantic annotation to a string representation."""

    logger.info(f"Processing annotation: {annotation}")

    if issubclass(annotation, BaseModel):
        return "section"

    if issubclass(annotation, Enum):
        return "enum"

    if isinstance(annotation, type):
        if annotation.__module__ == "builtins":
            return annotation.__name__
        return annotation.__module__ + "." + annotation.__qualname__

    return str(annotation)


def process_field(field_name: str, field: FieldInfo) -> Dict[str, Any]:
    """Extract field information from a Pydantic model."""
    field_type = annotation_to_type(field.annotation)

    field_info: dict[str, Any] = {
        "type": field_type,
        "title": field.title or field_name,
        "description": field.description or "",
    }
    if field_type != "section":
        field_info["default"] = field.default
        field_info["readonly"] = field.frozen or False
        if hasattr(field, "json_schema_extra") and field.json_schema_extra:
            if isinstance(field.json_schema_extra, dict):
                field_info.update(field.json_schema_extra)

    return field_info


def process_model(model: BaseModel) -> Dict[str, Any]:
    """Process a Pydantic model to extract its schema information."""

    output = {}

    for field_name, field in model.__class__.model_fields.items():
        if field_name == "name":
            # Skip the 'name' field as it is handled separately in the config
            continue
        processed_field = process_field(field_name, field)
        output[field_name] = processed_field
        if processed_field["type"] == "section":
            # Recursively process nested models
            nested_model = getattr(model, field_name)
            processed_field["fields"] = process_model(nested_model)

        else:
            processed_field["value"] = getattr(model, field_name, None)

        if (
            processed_field["type"] == "enum"
            and isinstance(field.annotation, type)
            and issubclass(field.annotation, Enum)
        ):
            processed_field["values"] = [
                e.value for e in field.annotation.__members__.values()
            ]

    return output


def config_to_wire(
    base_config: BaseModel,
    input_modules: dict[str, ModuleConfig],
    devices: dict[str, ModuleConfig],
) -> Dict[str, Any]:
    """Convert the base configuration and input modules to a wire-compatible format."""

    config = {
        "root": {
            "type": "section",
            "title": "Kalinka Player Configuration",
            "readonly": False,
            "fields": {
                "base_config": {
                    "type": "section",
                    "title": "Main Configuration",
                    "readonly": False,
                    "fields": process_model(base_config),
                },
                "input_modules": {
                    "type": "section",
                    "title": "Input Modules",
                    "fields": {
                        module.name: {
                            "type": "section",
                            "title": module.__class__.model_fields["name"].title,
                            "fields": process_model(module),
                        }
                        for module in input_modules.values()
                    },
                },
                "devices": {
                    "type": "section",
                    "title": "Devices",
                    "fields": {
                        device.name: {
                            "type": "section",
                            "title": device.__class__.model_fields["name"].title,
                            "fields": process_model(device),
                        }
                        for device in devices.values()
                    },
                },
            },
        }
    }
    return config

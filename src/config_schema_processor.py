"""Configuration schema processor for runtime type and validation info extraction."""

from typing import Any, Dict, List
from pydantic import BaseModel
from pydantic.fields import FieldInfo


def annotation_to_type(annotation: Any) -> str:
    """Convert a Pydantic annotation to a string representation."""
    if issubclass(annotation, BaseModel):
        return "section"

    if isinstance(annotation, type):
        if annotation.__module__ == "builtins":
            return annotation.__name__
        return annotation.__module__ + "." + annotation.__qualname__

    return str(annotation)


def process_field(field_name: str, field: FieldInfo) -> Dict[str, Any]:
    """Extract field information from a Pydantic model."""
    field_type = annotation_to_type(field.annotation)

    field_info = {
        "type": field_type,
        "title": field.title or field_name,
        "description": field.description or "",
    }
    if field_type != "section":
        field_info["default"] = field.default
        field_info["readonly"] = field.frozen or False  # type: ignore

    return field_info


def process_model(model: BaseModel) -> Dict[str, Any]:
    """Process a Pydantic model to extract its schema information."""

    output = {}

    for field_name, field in model.__class__.model_fields.items():
        processed_field = process_field(field_name, field)
        output[field_name] = processed_field
        if processed_field["type"] == "section":
            # Recursively process nested models
            nested_model = getattr(model, field_name)
            output[field_name]["fields"] = process_model(nested_model)

        else:
            processed_field["value"] = getattr(model, field_name, None)

    return output


def config_to_wire(
    base_config: BaseModel,
    input_modules: dict[str, BaseModel],
    devices: dict[str, BaseModel],
) -> Dict[str, Any]:
    """Convert the base configuration and input modules to a wire-compatible format."""
    config = {
        "base_config": process_model(base_config),
        "input_modules": {
            name: process_model(module) for name, module in input_modules.items()
        },
        "devices": {name: process_model(device) for name, device in devices.items()},
    }
    return config

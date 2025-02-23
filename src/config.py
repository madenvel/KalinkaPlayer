import json
import logging
import yaml

logger = logging.getLogger(__name__.split(".")[-1])


class Config:
    def __init__(self, config, schema):
        self.config_dict = yaml.safe_load(config)
        self.schema_dict = json.loads(schema)
        self._validate_config()

    def flatten_config(self):
        def flatten(node, schema_node, parent_key=""):
            items = {}
            if schema_node["type"] == "section":
                for k, v in schema_node.get("elements", {}).items():
                    new_key = f"{parent_key}.{k}" if parent_key else k
                    items.update(
                        flatten(None if node is None else node.get(k, None), v, new_key)
                    )
            else:
                value = node if node is not None else schema_node.get("default")
                items[parent_key] = value
            return items

        return flatten(self.config_dict, self.schema_dict)

    def __getitem__(self, key):
        keys = key.split(".")
        value = self.config_dict
        schema_value = self.schema_dict

        for k in keys:
            if schema_value.get("type") != "section":
                raise KeyError(f"Key {key} not found in configuration")

            schema_value = schema_value.get("elements", {})

            if k not in schema_value:
                raise KeyError(f"Key {key} not found in configuration")

            schema_value = schema_value[k]

            if k in value:
                value = value[k]
            elif "default" in schema_value:
                value = schema_value["default"]
            else:
                raise KeyError(f"Key {key} not found in configuration")

        return value

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def get_full_config(self):
        def add_values(node, schema_node):
            if schema_node["type"] == "section":
                result = {}
                for k, v in schema_node.get("elements", {}).items():
                    if k in node or v.get("required", "no") == "yes" or "default" in v:
                        result[k] = add_values(node.get(k, {}), v)
                return result
            else:
                default = schema_node.get("default", None)
                value = node if node is not None else default
                if value is None and schema_node.get("required", "no") == "no":
                    return None
                retval = {
                    "description": schema_node["description"],
                    "type": schema_node["type"],
                    "value": value,
                }
                if default is not None:
                    retval["default"] = default

                return retval

        return add_values(self.config_dict, self.schema_dict)

    def _validate_config(self):
        def validate_node(node, schema_node, path=""):
            required = schema_node.get("required", "no") in ["yes", True]

            if required and node is None:
                raise ValueError(
                    f"Missing required field: {schema_node['description']} at {path}"
                )

            if node is None:
                return

            if "type" not in schema_node:
                raise ValueError(
                    f"Type not defined for {schema_node['description']} at {path}"
                )

            node_type = schema_node["type"]

            if node_type == "section" and not isinstance(node, dict):
                raise TypeError(
                    f"Expected dict for {schema_node['description']} at {path}, got {type(node).__name__}"
                )
            elif node_type == "string" and not isinstance(node, str):
                raise TypeError(
                    f"Expected string for {schema_node['description']} at {path}, got {type(node).__name__}"
                )
            elif node_type == "integer" and not isinstance(node, int):
                raise TypeError(
                    f"Expected integer for {schema_node['description']} at {path}, got {type(node).__name__}"
                )
            elif node_type == "boolean" and not isinstance(node, bool):
                raise TypeError(
                    f"Expected boolean for {schema_node['description']} at {path}, got {type(node).__name__}"
                )
            elif node_type == "number" and not isinstance(node, (int, float)):
                raise TypeError(
                    f"Expected number for {schema_node['description']} at {path}, got {type(node).__name__}"
                )
            elif node_type == "enum" and node not in schema_node.get("values", []):
                raise ValueError(
                    f"Invalid value for {schema_node['description']} at {path}, got {node}, values {schema_node.get('values', [])}"
                )

            if node_type == "section":
                for k, v in schema_node.get("elements", {}).items():
                    validate_node(node.get(k), v, f"{path}.{k}")

        logger.info("Starting configuration validation")
        validate_node(self.config_dict, self.schema_dict)
        logger.info("Configuration validation completed")

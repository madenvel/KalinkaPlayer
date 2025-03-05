import json
import logging
import yaml

logger = logging.getLogger(__name__.split(".")[-1])


class Config:
    def __init__(self, config, schema, location):
        self.config_dict = yaml.safe_load(config)
        self.schema_dict = json.loads(schema)
        self.location = location
        self._validate_config()

    def dump(self):
        return yaml.safe_dump(self.config_dict)

    def save(self, location=None):
        if location is None:
            location = self.location
        with open(location, "w") as f:
            f.write(self.dump())

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

    def slice(self, key) -> "Config":
        keys = key.split(".")
        config_slice = self.config_dict
        schema_slice = self.schema_dict

        for k in keys:
            if schema_slice.get("type") != "section":
                raise KeyError(f"Key {key} not found in configuration schema")

            schema_slice = schema_slice.get("elements", {}).get(k)
            if schema_slice is None:
                raise KeyError(f"Key {key} not found in configuration schema")
            if schema_slice.get("type") != "section":
                raise KeyError(
                    f"Key {key} is not of type section in configuration schema"
                )

            config_slice = config_slice.get(k, {})

        # Create a new Config object with the sliced config and schema
        return Config(
            yaml.safe_dump(config_slice), json.dumps(schema_slice), self.location
        )

    def __getitem__(self, key):
        keys = key.split(".")
        value = self.config_dict
        schema_value = self.schema_dict

        for k in keys:
            if schema_value.get("type") != "section":
                raise KeyError(f"Key {key} not found in configuration")

            schema_value = schema_value.get("elements", {})

            if "#" in k:
                [split_key, suffix] = k.split("#")
            else:
                split_key, suffix = k, None

            if split_key not in schema_value:
                raise KeyError(f"Key {key} not found in configuration schema")

            schema_value = schema_value[split_key]

            if suffix is not None:
                if suffix in schema_value:
                    value = schema_value[suffix]
                    break
                else:
                    raise KeyError(f"Key {key} not found in configuration schema")

            if k in value:
                value = value[k]
            elif "default" in schema_value:
                value = schema_value["default"]
            else:
                raise KeyError(f"Key {key} not found in configuration")

        return value

    def __setitem__(self, key, value):
        keys = key.split(".")
        node = self.config_dict
        schema_node = self.schema_dict

        for k in keys[:-1]:
            if schema_node.get("type") != "section":
                raise KeyError(f"Key {key} not found in configuration")

            schema_node = schema_node.get("elements", {})

            if k not in schema_node:
                raise KeyError(f"Key {key} not found in configuration")

            schema_node = schema_node[k]

            if k not in node:
                node[k] = {}
            node = node[k]

        last_key = keys[-1]
        if last_key not in schema_node.get("elements", {}):
            raise KeyError(f"Key {key} not found in configuration")

        schema_node = schema_node["elements"][last_key]

        # Validate the value against the schema
        # Try to convert value to the appropriate type if needed
        node_type = schema_node["type"]
        try:
            if node_type == "string" or node_type == "password":
                value = str(value)
            elif node_type == "integer":
                value = int(value)
            elif node_type == "number":
                # Attempt to convert to int first, then float if that fails
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    value = float(value)
            elif node_type == "boolean":
                if isinstance(value, str):
                    value = value.lower()
                    if value in ("true", "yes", "1", "y"):
                        value = True
                    elif value in ("false", "no", "0", "n"):
                        value = False
                    else:
                        raise ValueError(f"Cannot convert '{value}' to boolean")
                else:
                    value = bool(value)
            elif node_type == "enum":
                if value not in schema_node.get("values", []):
                    raise ValueError(
                        f"Invalid value for {schema_node['description']}, got {value}, expected one of: {schema_node.get('values', [])}"
                    )
        except (ValueError, TypeError) as e:
            raise TypeError(
                f"Cannot convert value to {node_type} for {schema_node['description']}: {str(e)}"
            )

        # Validate the converted value
        self._validate_value(value, schema_node)

        node[last_key] = value

    def _validate_value(self, value, schema_node):
        node_type = schema_node["type"]

        if (node_type == "string" or node_type == "password") and not isinstance(
            value, str
        ):
            raise TypeError(
                f"Expected string for {schema_node['name']}, got {type(value).__name__}"
            )
        elif node_type == "integer" and not isinstance(value, int):
            raise TypeError(
                f"Expected integer for {schema_node['name']}, got {type(value).__name__}"
            )
        elif node_type == "boolean" and not isinstance(value, bool):
            raise TypeError(
                f"Expected boolean for {schema_node['name']}, got {type(value).__name__}"
            )
        elif node_type == "number" and not isinstance(value, (int, float)):
            raise TypeError(
                f"Expected number for {schema_node['name']}, got {type(value).__name__}"
            )
        elif node_type == "enum" and value not in schema_node.get("values", []):
            raise ValueError(
                f"Invalid value for {schema_node['name']}, got {value}, values {schema_node.get('values', [])}"
            )

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def get_full_config(self):
        def add_values(node, schema_node):
            if schema_node["type"] == "section":
                result = {
                    "name": schema_node.get("name", "No name"),
                    "description": schema_node.get("description", "No description"),
                    "type": "section",
                    "elements": {},
                }
                for k, v in schema_node.get("elements", {}).items():
                    if k in node or v.get("required", "no") == "yes" or "default" in v:
                        result["elements"][k] = add_values(node.get(k, {}), v)
                return result
            else:
                default = schema_node.get("default", None)
                value = node if node is not None else default
                if value is None and schema_node.get("required", "no") == "no":
                    return None
                node_type = schema_node["type"]
                retval = {
                    "name": schema_node.get("name", "No name"),
                    "description": schema_node.get("description", "No description"),
                    "type": node_type,
                    "value": value if node_type != "password" else "********",
                    "readonly": schema_node.get("readonly", "no").lower() == "yes",
                }
                if default is not None:
                    retval["default"] = default

                if node_type == "enum":
                    retval["values"] = schema_node.get("values", [])

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

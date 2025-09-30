from kalinka_plugin_sdk.api import PluginContext  # runtime Protocols
{%- if cookiecutter.plugin_type == "input_module" %}
from kalinka_plugin_sdk.inputmodule import InputModule
{%- endif %}
{%- if cookiecutter.plugin_type == "device" %}
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
{%- endif %}

from .config_model import {{ cookiecutter.plugin_class_prefix }}Config
{%- if cookiecutter.plugin_type == "input_module" %}
from .{{ cookiecutter.plugin_id }}_input_module import {{ cookiecutter.plugin_class_prefix }}InputModule
{%- endif %}
{%- if cookiecutter.plugin_type == "device" %}
from .{{ cookiecutter.plugin_id }}_device import {{ cookiecutter.plugin_class_prefix }}Device
{%- endif %}


REQUIRES_SDK = "{{ cookiecutter.sdk_version_constraint }}"
PLUGIN_ID = "{{ cookiecutter.name }}"

Config = {{ cookiecutter.plugin_class_prefix }}Config


def setup(
    cfg: {{ cookiecutter.plugin_class_prefix }}Config, ctx: "PluginContext"
) -> {% if cookiecutter.plugin_type == "input_module" %}InputModule{% else %}ExternalOutputDevice{% endif %}:
    """
    Entry point used by Kalinka. Register subscriptions, timers, etc.
    This function must not block.
    """
    ctx.logger.info("plugin_setup", plugin=PLUGIN_ID, version=ctx.sdk_version)

{%- if cookiecutter.plugin_type == "input_module" %}
    return {{ cookiecutter.plugin_class_prefix }}InputModule(cfg)
{%- else %}
    return {{ cookiecutter.plugin_class_prefix }}Device(cfg)
{%- endif %}

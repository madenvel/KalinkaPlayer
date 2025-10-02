from typing import Optional
from kalinka_plugin_sdk.api import PluginContext  # runtime Protocols
{%- if cookiecutter.plugin_type == "input_module" %}
from kalinka_plugin_sdk.inputmodule import InputModule
from kalinka_plugin_sdk.api import InputModulePlugin
{%- endif %}
{%- if cookiecutter.plugin_type == "device" %}
from kalinka_plugin_sdk.ext_device import ExternalOutputDevice
from kalinka_plugin_sdk.api import OutputDevicePlugin
{%- endif %}

from .config_model import {{ cookiecutter.plugin_class_prefix }}Config
{%- if cookiecutter.plugin_type == "input_module" %}
from .{{ cookiecutter.plugin_id }}_input_module import {{ cookiecutter.plugin_class_prefix }}InputModule
{%- endif %}
{%- if cookiecutter.plugin_type == "device" %}
from .{{ cookiecutter.plugin_id }}_device import {{ cookiecutter.plugin_class_prefix }}Device
{%- endif %}


class KalinkaPlugin{{ cookiecutter.plugin_class_prefix }}({% if cookiecutter.plugin_type == "input_module" %}InputModulePlugin{% else %}OutputDevicePlugin{% endif %}):
    """
    Kalinka plugin class. This is the main entry point for the plugin.
    """
    REQUIRES_SDK = "{{ cookiecutter.sdk_version_constraint }}"
    PLUGIN_ID = "{{ cookiecutter.name }}"
    CONFIG_MODEL = {{ cookiecutter.plugin_class_prefix }}Config

    def __init__(self):
{%- if cookiecutter.plugin_type == "input_module" %}
        self.interface = None
{%- else %}
        self._device = None
{%- endif %}

    def get_interface(self) -> Optional[{% if cookiecutter.plugin_type == "input_module" %}InputModule{% else %}ExternalOutputDevice{% endif %}]:
{%- if cookiecutter.plugin_type == "input_module" %}
        return self.interface
{%- else %}
        return self._device
{%- endif %}

    def setup(self, context: PluginContext) -> None:
        """
        Entry point used by Kalinka. Register subscriptions, timers, etc.
        This function must not block.
        """
        config = {{ cookiecutter.plugin_class_prefix }}Config(**context.config.model_dump())
{%- if cookiecutter.plugin_type == "input_module" %}
        self.interface = {{ cookiecutter.plugin_class_prefix }}InputModule(config)
{%- else %}
        self._device = {{ cookiecutter.plugin_class_prefix }}Device(config)
{%- endif %}
        context.logger.info("plugin_setup", plugin=self.PLUGIN_ID, version=context.sdk_version)

    def shutdown(self) -> None:
        """
        Entry point used by Kalinka when unloading the plugin. Clean up resources here.
        Should not raise exceptions - log errors instead.
        """
{%- if cookiecutter.plugin_type == "input_module" %}
        self.interface = None
{%- else %}
        self._device = None
{%- endif %}

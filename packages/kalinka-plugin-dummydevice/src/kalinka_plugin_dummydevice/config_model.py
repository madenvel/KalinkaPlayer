from pydantic import Field
from kalinka_plugin_sdk.module_config import ModuleConfig


class DummydeviceConfig(ModuleConfig):
    name: str = Field(
        default="dummydevice",
        title="Kalinka Plugin Dummydevice",
        frozen=True,
        exclude=True,
    )
    enabled: bool = Field(default=False, title="Module Enabled")

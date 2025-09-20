from pydantic import Field
from src.base_config_model import ModuleConfig


class DummyDeviceConfig(ModuleConfig):
    name: str = Field(default="dummy", title="Dummy Device", frozen=True, exclude=True)
    enabled: bool = Field(default=False, title="Module Enabled")

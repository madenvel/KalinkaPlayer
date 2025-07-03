from pydantic import BaseModel, Field
import uuid


class ModuleConfig(BaseModel):
    """Base class for input modules."""

    name: str = Field(
        default_factory=lambda: f"module_{uuid.uuid4().hex[:8]}",
        title="Kalinka Module Name",
        frozen=True,
        exclude=True,
    )
    enabled: bool = Field(default=True, title="Module enabled")

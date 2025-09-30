from typing import List, Optional

from pydantic import BaseModel, PositiveInt

from kalinka_plugin_sdk.datamodel import Track


class ErrorResponse(BaseModel):
    error: str
    message: str
    status_code: int


class SuccessResponse(BaseModel):
    message: str
    status_code: int

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class EntityPublic(ApiModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class MessageResponse(ApiModel):
    message: str


class Page[T](ApiModel):
    data: list[T]
    count: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ClientEventType = Literal[
    "session.resume",
    "user.text.submit",
    "session.pause",
    "session.finish",
]


class ClientEvent(BaseModel):
    event_id: uuid.UUID
    type: ClientEventType
    sequence: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ServerEvent(BaseModel):
    event_id: uuid.UUID
    session_id: uuid.UUID
    type: str
    sequence: int = Field(ge=0)
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    transient: bool = False

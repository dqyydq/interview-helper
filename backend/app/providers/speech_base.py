from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio: bytes
    filename: str
    content_type: str
    language: str | None = None
    prompt: str | None = None


class TranscriptionResult(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    provider_request_id: str | None = None


class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...

"""Validated request models for the HTTP API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str = Field(default="Спикер не определён", max_length=160)
    text: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def check_time_order(self) -> "TranscriptSegment":
        if self.end < self.start:
            raise ValueError("Время окончания фрагмента не может быть раньше начала.")
        return self


class MeetingExportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["pdf", "docx"]
    title: str = Field(default="Протокол совещания", min_length=1, max_length=160)
    date: date
    summary: str = Field(default="", max_length=20_000)
    actions: list[dict[str, object]] = Field(default_factory=list, max_length=2_000)
    transcript: list[TranscriptSegment] = Field(default_factory=list, max_length=20_000)
    participants: list[dict[str, object]] = Field(default_factory=list, max_length=200)
    summary_items: list[dict[str, object]] = Field(default_factory=list, max_length=500)

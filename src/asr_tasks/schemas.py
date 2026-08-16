from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

Hotword = Annotated[str, Field(min_length=1, max_length=128)]


class AssetResponse(BaseModel):
    id: str
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str
    created_at: datetime


class GlossaryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    hotwords: list[Hotword] = Field(default_factory=list, max_length=1_000)
    replacements: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_replacements(self) -> GlossaryCreate:
        if len(self.replacements) > 1_000:
            raise ValueError("replacements may contain at most 1000 entries")
        if any(not source or not target for source, target in self.replacements.items()):
            raise ValueError("replacement keys and values must not be empty")
        return self


class GlossaryResponse(GlossaryCreate):
    id: str
    created_at: datetime


class JobCreate(BaseModel):
    asset_id: str | None = None
    source_url: str | None = None
    client_request_id: str | None = Field(default=None, max_length=255)
    glossary_id: str | None = None
    hotwords: list[Hotword] = Field(default_factory=list, max_length=1_000)
    replacements: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> JobCreate:
        if (self.asset_id is None) == (self.source_url is None):
            raise ValueError("exactly one of asset_id and source_url is required")
        if len(self.replacements) > 1_000:
            raise ValueError("replacements may contain at most 1000 entries")
        return self


class BatchJobCreate(BaseModel):
    jobs: list[JobCreate] = Field(min_length=1, max_length=1_000)


class JobAccepted(BaseModel):
    id: str
    status: str
    created_at: datetime


class BatchJobAccepted(BaseModel):
    jobs: list[JobAccepted]


class JobStatusResponse(BaseModel):
    id: str
    status: str
    stage: str
    progress: float
    processed_seconds: float
    total_seconds: float
    queue_position: int | None
    heartbeat_at: datetime | None
    alive: bool
    cancel_requested: bool
    partial_text: str
    error: dict[str, str | None] | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class SegmentResult(BaseModel):
    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    replacements: list[dict]


class JobResultResponse(BaseModel):
    id: str
    text: str
    segments: list[SegmentResult]
    metadata: dict


class CancelResponse(BaseModel):
    id: str
    status: Literal["cancel_requested", "cancelled"]


class HealthResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    database: bool = True
    worker_ready: bool | None = None
    model_ready: bool | None = None

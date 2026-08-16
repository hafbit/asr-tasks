from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    SEGMENTING = "segmenting"
    TRANSCRIBING = "transcribing"
    POSTPROCESSING = "postprocessing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SegmentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("asset"))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Glossary(Base):
    __tablename__ = "glossaries"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("glossary")
    )
    name: Mapped[str] = mapped_column(String(255))
    hotwords: Mapped[list[str]] = mapped_column(JSON, default=list)
    replacements: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    glossary_id: Mapped[str | None] = mapped_column(ForeignKey("glossaries.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), default="paraformer-contextual")
    hotwords: Mapped[list[str]] = mapped_column(JSON, default=list)
    replacements: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value, index=True)
    stage: Mapped[str] = mapped_column(String(32), default=JobStage.QUEUED.value)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    total_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    processed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    partial_text: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped[Asset | None] = relationship()
    glossary: Mapped[Glossary | None] = relationship()
    segments: Mapped[list[TranscriptionSegment]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="TranscriptionSegment.ordinal"
    )

    __table_args__ = (Index("ix_jobs_claim", "status", "lease_expires_at", "created_at"),)


class TranscriptionSegment(Base):
    __tablename__ = "transcription_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default=SegmentStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    replacements: Mapped[list[dict]] = mapped_column(JSON, default=list)

    job: Mapped[TranscriptionJob] = relationship(back_populates="segments")

    __table_args__ = (UniqueConstraint("job_id", "ordinal", name="uq_segment_job_ordinal"),)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerState(Base):
    __tablename__ = "worker_states"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    current_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

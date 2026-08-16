from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import Base, create_db_engine, create_session_factory
from .models import (
    Asset,
    Glossary,
    JobStage,
    JobStatus,
    SegmentStatus,
    TranscriptionJob,
    TranscriptionSegment,
    new_id,
)
from .repository import queue_position, worker_is_ready
from .schemas import (
    AssetResponse,
    BatchJobAccepted,
    BatchJobCreate,
    CancelResponse,
    GlossaryCreate,
    GlossaryResponse,
    HealthResponse,
    JobAccepted,
    JobCreate,
    JobResultResponse,
    JobStatusResponse,
    SegmentResult,
)
from .security import auth_dependency, validate_source_url

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    Base.metadata.create_all(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(
        title="asr-tasks",
        version="0.1.0",
        description="异步长视频普通话转写任务服务",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    authenticate = auth_dependency(settings)

    def get_session(request: Request):  # type: ignore[no-untyped-def]
        session = request.app.state.session_factory()
        try:
            yield session
        finally:
            session.close()

    def get_job(session: Session, job_id: str) -> TranscriptionJob:
        job = session.get(TranscriptionJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="transcription job not found")
        return job

    def accepted(job: TranscriptionJob) -> JobAccepted:
        return JobAccepted(id=job.id, status=job.status, created_at=job.created_at)

    def create_job_record(
        session: Session, payload: JobCreate, idempotency_key: str | None = None
    ) -> TranscriptionJob:
        client_request_id = payload.client_request_id or idempotency_key
        if client_request_id:
            existing = session.scalar(
                select(TranscriptionJob).where(
                    TranscriptionJob.client_request_id == client_request_id
                )
            )
            if existing:
                return existing
        if payload.asset_id and session.get(Asset, payload.asset_id) is None:
            raise HTTPException(status_code=404, detail="asset not found")
        if payload.glossary_id and session.get(Glossary, payload.glossary_id) is None:
            raise HTTPException(status_code=404, detail="glossary not found")
        if payload.source_url:
            try:
                validate_source_url(payload.source_url, settings)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        job = TranscriptionJob(
            asset_id=payload.asset_id,
            source_url=payload.source_url,
            client_request_id=client_request_id,
            glossary_id=payload.glossary_id,
            hotwords=list(dict.fromkeys(payload.hotwords)),
            replacements=payload.replacements,
        )
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if not client_request_id:
                raise
            existing = session.scalar(
                select(TranscriptionJob).where(
                    TranscriptionJob.client_request_id == client_request_id
                )
            )
            if existing is None:
                raise
            return existing
        session.refresh(job)
        return job

    @app.get("/health/live", response_model=HealthResponse)
    def health_live(session: Session = Depends(get_session)) -> HealthResponse:
        session.execute(text("SELECT 1"))
        return HealthResponse(status="ok", database=True)

    @app.get("/health/ready", response_model=HealthResponse)
    def health_ready(session: Session = Depends(get_session)) -> HealthResponse:
        session.execute(text("SELECT 1"))
        ready = worker_is_ready(session, settings.worker_stale_seconds)
        if not ready:
            raise HTTPException(status_code=503, detail="no ready ASR worker")
        return HealthResponse(status="ok", database=True, worker_ready=True, model_ready=True)

    @app.post(
        "/v1/assets",
        response_model=AssetResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(authenticate)],
    )
    async def upload_asset(
        file: UploadFile = File(...), session: Session = Depends(get_session)
    ) -> AssetResponse:
        asset_id = new_id("asset")
        safe_suffix = Path(file.filename or "upload.bin").suffix[:16]
        target = settings.assets_dir / f"{asset_id}{safe_suffix}"
        temporary = target.with_suffix(f"{target.suffix}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="uploaded asset is too large")
                    digest.update(chunk)
                    output.write(chunk)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        asset = Asset(
            id=asset_id,
            filename=Path(file.filename or "upload.bin").name,
            content_type=file.content_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
            path=str(target),
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return AssetResponse.model_validate(asset, from_attributes=True)

    @app.post(
        "/v1/glossaries",
        response_model=GlossaryResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(authenticate)],
    )
    def create_glossary(
        payload: GlossaryCreate, session: Session = Depends(get_session)
    ) -> GlossaryResponse:
        glossary = Glossary(
            name=payload.name,
            hotwords=list(dict.fromkeys(payload.hotwords)),
            replacements=payload.replacements,
        )
        session.add(glossary)
        session.commit()
        session.refresh(glossary)
        return GlossaryResponse.model_validate(glossary, from_attributes=True)

    @app.post(
        "/v1/transcription-jobs",
        response_model=JobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(authenticate)],
    )
    def create_job(
        payload: JobCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        session: Session = Depends(get_session),
    ) -> JobAccepted:
        return accepted(create_job_record(session, payload, idempotency_key))

    @app.post(
        "/v1/transcription-jobs:batch",
        response_model=BatchJobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(authenticate)],
    )
    def create_batch(
        payload: BatchJobCreate, session: Session = Depends(get_session)
    ) -> BatchJobAccepted:
        jobs = [accepted(create_job_record(session, item)) for item in payload.jobs]
        return BatchJobAccepted(jobs=jobs)

    @app.get(
        "/v1/transcription-jobs/{job_id}",
        response_model=JobStatusResponse,
        dependencies=[Depends(authenticate)],
    )
    def job_status(job_id: str, session: Session = Depends(get_session)) -> JobStatusResponse:
        job = get_job(session, job_id)
        alive = True
        if job.status == JobStatus.RUNNING.value:
            cutoff = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
            heartbeat = job.heartbeat_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            alive = heartbeat is not None and heartbeat >= cutoff
        error = None
        if job.error_code or job.error_message:
            error = {"code": job.error_code, "message": job.error_message}
        return JobStatusResponse(
            id=job.id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            processed_seconds=job.processed_seconds,
            total_seconds=job.total_seconds,
            queue_position=queue_position(session, job),
            heartbeat_at=job.heartbeat_at,
            alive=alive,
            cancel_requested=job.cancel_requested,
            partial_text=job.partial_text,
            error=error,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    @app.get(
        "/v1/transcription-jobs/{job_id}/result",
        response_model=JobResultResponse,
        dependencies=[Depends(authenticate)],
    )
    def job_result(job_id: str, session: Session = Depends(get_session)) -> JobResultResponse:
        job = get_job(session, job_id)
        if job.status != JobStatus.SUCCEEDED.value:
            raise HTTPException(status_code=409, detail="transcription job has not succeeded")
        segments = session.scalars(
            select(TranscriptionSegment)
            .where(
                TranscriptionSegment.job_id == job.id,
                TranscriptionSegment.status == SegmentStatus.COMPLETED.value,
            )
            .order_by(TranscriptionSegment.ordinal)
        ).all()
        return JobResultResponse(
            id=job.id,
            text=job.result_text or "",
            segments=[
                SegmentResult(
                    ordinal=item.ordinal,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    text=item.text,
                    replacements=item.replacements,
                )
                for item in segments
            ],
            metadata=job.result_metadata,
        )

    @app.post(
        "/v1/transcription-jobs/{job_id}/cancel",
        response_model=CancelResponse,
        dependencies=[Depends(authenticate)],
    )
    def cancel_job(job_id: str, session: Session = Depends(get_session)) -> CancelResponse:
        job = get_job(session, job_id)
        if job.status == JobStatus.QUEUED.value:
            job.status = JobStatus.CANCELLED.value
            job.stage = JobStage.CANCELLED.value
            job.cancel_requested = True
            job.completed_at = datetime.now(UTC)
            response_status = "cancelled"
        elif job.status == JobStatus.RUNNING.value:
            job.cancel_requested = True
            response_status = "cancel_requested"
        elif job.status == JobStatus.CANCELLED.value:
            response_status = "cancelled"
        else:
            raise HTTPException(status_code=409, detail="completed job cannot be cancelled")
        session.add(job)
        session.commit()
        return CancelResponse(id=job.id, status=response_status)

    return app

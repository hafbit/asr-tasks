from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    JobEvent,
    JobStage,
    JobStatus,
    TranscriptionJob,
    WorkerState,
    utcnow,
)

TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


def set_job_progress(
    session: Session,
    job: TranscriptionJob,
    *,
    stage: JobStage | str,
    progress: float,
    processed_seconds: float | None = None,
    total_seconds: float | None = None,
    partial_text: str | None = None,
) -> None:
    stage_value = stage.value if isinstance(stage, JobStage) else stage
    job.stage = stage_value
    job.progress = min(100.0, max(job.progress, round(progress, 3)))
    if processed_seconds is not None:
        job.processed_seconds = max(job.processed_seconds, processed_seconds)
    if total_seconds is not None:
        job.total_seconds = max(job.total_seconds, total_seconds)
    if partial_text is not None:
        job.partial_text = partial_text
    job.updated_at = utcnow()
    session.add(job)


def add_event(session: Session, job_id: str, kind: str, payload: dict | None = None) -> None:
    session.add(JobEvent(job_id=job_id, kind=kind, payload=payload or {}))


def claim_next_job(
    engine: Engine,
    factory: sessionmaker[Session],
    *,
    worker_id: str,
    lease_seconds: int,
) -> TranscriptionJob | None:
    now = utcnow()
    expires = now + timedelta(seconds=lease_seconds)
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        row = connection.execute(
            text(
                """
                SELECT id FROM transcription_jobs
                WHERE status = :queued
                   OR (status = :running AND lease_expires_at IS NOT NULL
                       AND lease_expires_at < :now)
                ORDER BY CASE WHEN status = :running THEN 0 ELSE 1 END, created_at
                LIMIT 1
                """
            ),
            {
                "queued": JobStatus.QUEUED.value,
                "running": JobStatus.RUNNING.value,
                "now": now,
            },
        ).first()
        if row is None:
            connection.commit()
            return None
        job_id = row[0]
        connection.execute(
            text(
                """
                UPDATE transcription_jobs
                SET status = :running, lease_owner = :owner,
                    lease_expires_at = :expires, heartbeat_at = :now,
                    started_at = COALESCE(started_at, :now), updated_at = :now
                WHERE id = :job_id
                """
            ),
            {
                "running": JobStatus.RUNNING.value,
                "owner": worker_id,
                "expires": expires,
                "now": now,
                "job_id": job_id,
            },
        )
        connection.commit()

    with factory() as session:
        return session.get(TranscriptionJob, job_id)


def renew_worker_lease(
    factory: sessionmaker[Session],
    *,
    worker_id: str,
    current_job_id: str | None,
    lease_seconds: int,
    model_ready: bool,
    detail: dict | None = None,
) -> None:
    now = utcnow()
    with factory() as session:
        state = session.get(WorkerState, worker_id) or WorkerState(id=worker_id)
        state.model_ready = model_ready
        state.current_job_id = current_job_id
        state.heartbeat_at = now
        state.detail = detail or {}
        session.add(state)
        if current_job_id:
            job = session.get(TranscriptionJob, current_job_id)
            if job and job.lease_owner == worker_id and job.status == JobStatus.RUNNING.value:
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=lease_seconds)
                session.add(job)
        session.commit()


def queue_position(session: Session, job: TranscriptionJob) -> int | None:
    if job.status != JobStatus.QUEUED.value:
        return None
    return int(
        session.scalar(
            select(func.count(TranscriptionJob.id)).where(
                TranscriptionJob.status == JobStatus.QUEUED.value,
                TranscriptionJob.created_at < job.created_at,
            )
        )
        or 0
    )


def worker_is_ready(session: Session, stale_seconds: int) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    return bool(
        session.scalar(
            select(func.count(WorkerState.id)).where(
                WorkerState.model_ready.is_(True), WorkerState.heartbeat_at >= cutoff
            )
        )
    )


def mark_terminal(
    session: Session,
    job: TranscriptionJob,
    *,
    status: JobStatus,
    stage: JobStage,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status.value
    job.stage = stage.value
    job.error_code = error_code
    job.error_message = error_message
    job.lease_owner = None
    job.lease_expires_at = None
    job.completed_at = utcnow()
    job.updated_at = job.completed_at
    if status == JobStatus.SUCCEEDED:
        job.progress = 100.0
    session.add(job)

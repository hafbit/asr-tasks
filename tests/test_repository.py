from __future__ import annotations

from datetime import timedelta

from asr_tasks.db import Base, create_db_engine, create_session_factory
from asr_tasks.models import JobStage, TranscriptionJob, utcnow
from asr_tasks.repository import claim_next_job, set_job_progress


def test_progress_is_monotonic(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        job = TranscriptionJob(source_url="https://example.com/a")
        session.add(job)
        session.commit()
        set_job_progress(session, job, stage=JobStage.EXTRACTING, progress=10)
        set_job_progress(session, job, stage=JobStage.EXTRACTING, progress=5)
        session.commit()
        assert job.progress == 10


def test_stale_job_is_reclaimed(settings) -> None:  # type: ignore[no-untyped-def]
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        job = TranscriptionJob(
            source_url="https://example.com/a",
            status="running",
            lease_owner="dead-worker",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    claimed = claim_next_job(engine, factory, worker_id="new-worker", lease_seconds=10)
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.lease_owner == "new-worker"

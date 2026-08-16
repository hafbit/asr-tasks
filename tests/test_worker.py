from __future__ import annotations

import numpy as np
import soundfile as sf

from asr_tasks.models import (
    Asset,
    JobStatus,
    SegmentStatus,
    TranscriptionJob,
    TranscriptionSegment,
)
from asr_tasks.worker import Worker


class FakeEngine:
    def detect_speech(self, audio: np.ndarray, sample_rate: int) -> list[tuple[int, int]]:
        duration_ms = int(len(audio) * 1000 / sample_rate)
        midpoint = duration_ms // 2
        return [(0, midpoint), (midpoint, duration_ms)]

    def transcribe_batch(self, audio: list[np.ndarray], *, hotwords: list[str]) -> list[str]:
        assert "深度学习" in hotwords
        return ["深度学席"] * len(audio)

    def punctuate(self, text: str) -> str:
        return f"{text}。" if text else text


class CountingEngine(FakeEngine):
    def __init__(self) -> None:
        self.transcribed_segments = 0

    def transcribe_batch(self, audio: list[np.ndarray], *, hotwords: list[str]) -> list[str]:
        self.transcribed_segments += len(audio)
        return super().transcribe_batch(audio, hotwords=hotwords)


def test_worker_completes_job_and_exposes_result(settings, client, app, auth_headers) -> None:  # type: ignore[no-untyped-def]
    audio_path = settings.assets_dir / "sample.wav"
    settings.ensure_directories()
    sf.write(audio_path, np.zeros(4 * 16_000, dtype=np.float32), 16_000)

    factory = app.state.session_factory
    with factory() as session:
        asset = Asset(
            filename="sample.wav",
            content_type="audio/wav",
            size_bytes=audio_path.stat().st_size,
            sha256="0" * 64,
            path=str(audio_path),
        )
        session.add(asset)
        session.flush()
        job = TranscriptionJob(asset_id=asset.id, hotwords=["深度学习"])
        session.add(job)
        session.commit()
        job_id = job.id

    worker = Worker(
        settings,
        engine=app.state.engine,
        session_factory=factory,
        asr_engine=FakeEngine(),
        worker_id="test-worker",
    )
    assert worker.run_once() is True

    response = client.get(f"/v1/transcription-jobs/{job_id}", headers=auth_headers)
    assert response.json()["status"] == JobStatus.SUCCEEDED.value
    assert response.json()["progress"] == 100
    result = client.get(f"/v1/transcription-jobs/{job_id}/result", headers=auth_headers).json()
    assert result["text"] == "深度学习深度学习。"
    assert len(result["segments"]) == 2


def test_worker_resumes_after_completed_batch(settings, app) -> None:  # type: ignore[no-untyped-def]
    audio_path = settings.assets_dir / "resume.wav"
    settings.ensure_directories()
    sf.write(audio_path, np.zeros(4 * 16_000, dtype=np.float32), 16_000)
    factory = app.state.session_factory
    with factory() as session:
        asset = Asset(
            filename="resume.wav",
            content_type="audio/wav",
            size_bytes=audio_path.stat().st_size,
            sha256="1" * 64,
            path=str(audio_path),
        )
        session.add(asset)
        session.flush()
        job = TranscriptionJob(asset_id=asset.id, hotwords=["深度学习"])
        session.add(job)
        session.flush()
        session.add_all(
            [
                TranscriptionSegment(
                    job_id=job.id,
                    ordinal=0,
                    start_ms=0,
                    end_ms=2_000,
                    status=SegmentStatus.COMPLETED.value,
                    attempts=1,
                    text="已完成批次",
                ),
                TranscriptionSegment(job_id=job.id, ordinal=1, start_ms=2_000, end_ms=4_000),
            ]
        )
        session.commit()
        job_id = job.id

    fake = CountingEngine()
    worker = Worker(
        settings,
        engine=app.state.engine,
        session_factory=factory,
        asr_engine=fake,
        worker_id="resume-worker",
    )
    assert worker.run_once() is True
    assert fake.transcribed_segments == 1
    with factory() as session:
        job = session.get(TranscriptionJob, job_id)
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED.value
        assert job.result_text == "已完成批次深度学习。"

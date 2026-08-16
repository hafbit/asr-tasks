from __future__ import annotations

import json
import logging
import shutil
import socket
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from .asr_engine import ASREngine, FunASREngine
from .config import Settings
from .db import Base, create_db_engine, create_session_factory
from .glossary import apply_terminology
from .media import (
    CancelledError,
    MediaError,
    canonicalize_vad_segments,
    download_source,
    extract_audio,
    iter_audio_windows,
    probe_media,
    read_audio_segments,
)
from .models import (
    Asset,
    JobStage,
    JobStatus,
    SegmentStatus,
    TranscriptionJob,
    TranscriptionSegment,
)
from .repository import (
    add_event,
    claim_next_job,
    mark_terminal,
    renew_worker_lease,
    set_job_progress,
)

logger = logging.getLogger(__name__)


class JobCancelled(CancelledError):
    pass


class Worker:
    def __init__(
        self,
        settings: Settings,
        *,
        engine: Engine | None = None,
        session_factory: sessionmaker[Session] | None = None,
        asr_engine: ASREngine | None = None,
        engine_factory: Callable[[Settings], ASREngine] = FunASREngine,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.engine = engine or create_db_engine(settings)
        Base.metadata.create_all(self.engine)
        self.sessions = session_factory or create_session_factory(self.engine)
        self.asr_engine = asr_engine
        self.engine_factory = engine_factory
        self.worker_id = worker_id or (f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}")
        self.current_job_id: str | None = None
        self.model_ready = asr_engine is not None
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    def ensure_model(self) -> None:
        if self.asr_engine is None:
            logger.info("loading FunASR models", extra={"worker_id": self.worker_id})
            self.asr_engine = self.engine_factory(self.settings)
        self.model_ready = True
        self._beat()

    def _beat(self) -> None:
        renew_worker_lease(
            self.sessions,
            worker_id=self.worker_id,
            current_job_id=self.current_job_id,
            lease_seconds=self.settings.worker_stale_seconds,
            model_ready=self.model_ready,
            detail={"cpu_threads": self.settings.cpu_threads},
        )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.settings.heartbeat_interval_seconds):
            try:
                self._beat()
            except Exception:
                logger.exception("worker heartbeat failed")

    def start_heartbeat(self) -> None:
        if self._heartbeat and self._heartbeat.is_alive():
            return
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name=f"heartbeat-{self.worker_id}",
            daemon=True,
        )
        self._heartbeat.start()
        self._beat()

    def stop(self) -> None:
        self._stop.set()
        if self._heartbeat:
            self._heartbeat.join(timeout=2)

    def run(self) -> None:
        self.start_heartbeat()
        try:
            self.ensure_model()
            while not self._stop.is_set():
                if not self.run_once():
                    self._stop.wait(self.settings.poll_interval_seconds)
        finally:
            self.current_job_id = None
            self.model_ready = False
            self._beat()
            self.stop()

    def run_once(self) -> bool:
        self.ensure_model()
        job = claim_next_job(
            self.engine,
            self.sessions,
            worker_id=self.worker_id,
            lease_seconds=self.settings.worker_stale_seconds,
        )
        if job is None:
            return False
        self.current_job_id = job.id
        self._beat()
        try:
            self.process_job(job.id)
        except JobCancelled as error:
            logger.info("job cancelled", extra={"job_id": job.id})
            with self.sessions() as session:
                current = session.get(TranscriptionJob, job.id)
                if current:
                    mark_terminal(
                        session,
                        current,
                        status=JobStatus.CANCELLED,
                        stage=JobStage.CANCELLED,
                        error_message=str(error),
                    )
                    add_event(session, job.id, "cancelled")
                    session.commit()
        except Exception as error:
            logger.exception("job failed", extra={"job_id": job.id})
            with self.sessions() as session:
                current = session.get(TranscriptionJob, job.id)
                if current:
                    mark_terminal(
                        session,
                        current,
                        status=JobStatus.FAILED,
                        stage=JobStage.FAILED,
                        error_code=error.__class__.__name__,
                        error_message=str(error)[:4_000],
                    )
                    add_event(
                        session,
                        job.id,
                        "failed",
                        {"type": error.__class__.__name__, "message": str(error)[:1_000]},
                    )
                    session.commit()
        finally:
            self.current_job_id = None
            self._beat()
        return True

    def _cancelled(self, job_id: str) -> bool:
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            return job is None or job.cancel_requested

    def _require_not_cancelled(self, job_id: str) -> None:
        if self._cancelled(job_id):
            raise JobCancelled("cancellation requested")

    def _update_progress(
        self,
        job_id: str,
        stage: JobStage,
        progress: float,
        *,
        processed_seconds: float | None = None,
        total_seconds: float | None = None,
        partial_text: str | None = None,
    ) -> None:
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job disappeared")
            set_job_progress(
                session,
                job,
                stage=stage,
                progress=progress,
                processed_seconds=processed_seconds,
                total_seconds=total_seconds,
                partial_text=partial_text,
            )
            session.commit()

    def _resolve_source(self, job_id: str) -> Path:
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job not found")
            if job.source_path and Path(job.source_path).is_file():
                return Path(job.source_path)
            if job.asset_id:
                asset = session.get(Asset, job.asset_id)
                if asset is None or not Path(asset.path).is_file():
                    raise MediaError("asset file is missing")
                job.source_path = asset.path
                session.add(job)
                session.commit()
                return Path(asset.path)
            source_url = job.source_url
        if not source_url:
            raise MediaError("job does not have a media source")
        job_dir = self.settings.work_dir / job_id
        source = download_source(
            source_url,
            job_dir,
            self.settings,
            progress=lambda value: self._update_progress(job_id, JobStage.DOWNLOADING, value * 5),
            cancelled=lambda: self._cancelled(job_id),
        )
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job disappeared")
            job.source_path = str(source)
            session.add(job)
            session.commit()
        return source

    def _ensure_audio(self, job_id: str, source: Path) -> tuple[Path, float]:
        info = probe_media(source)
        self._update_progress(job_id, JobStage.EXTRACTING, 5.0, total_seconds=info.duration_seconds)
        audio_path = self.settings.work_dir / job_id / "audio.flac"
        if not audio_path.is_file():
            extract_audio(
                source,
                audio_path,
                duration_seconds=info.duration_seconds,
                progress=lambda value: self._update_progress(
                    job_id,
                    JobStage.EXTRACTING,
                    5 + value * 5,
                    total_seconds=info.duration_seconds,
                ),
                cancelled=lambda: self._cancelled(job_id),
            )
        else:
            self._update_progress(job_id, JobStage.EXTRACTING, 10.0)
        return audio_path, info.duration_seconds

    def _ensure_segments(self, job_id: str, audio_path: Path, duration: float) -> None:
        with self.sessions() as session:
            existing = session.scalar(
                select(func.count(TranscriptionSegment.id)).where(
                    TranscriptionSegment.job_id == job_id
                )
            )
        if existing:
            self._update_progress(job_id, JobStage.SEGMENTING, 15.0)
            return
        assert self.asr_engine is not None
        boundaries: list[tuple[int, int]] = []
        windows = max(
            1, int((duration + self.settings.window_seconds - 1) // self.settings.window_seconds)
        )
        for window in iter_audio_windows(
            audio_path,
            window_seconds=self.settings.window_seconds,
            overlap_seconds=self.settings.window_overlap_seconds,
        ):
            self._require_not_cancelled(job_id)
            relative = self.asr_engine.detect_speech(window.samples, window.sample_rate)
            boundaries.extend(canonicalize_vad_segments(window, relative))
            self._update_progress(
                job_id,
                JobStage.SEGMENTING,
                10 + min(1.0, (window.index + 1) / windows) * 5,
            )
        boundaries.sort()
        with self.sessions() as session:
            for ordinal, (start_ms, end_ms) in enumerate(boundaries):
                session.add(
                    TranscriptionSegment(
                        job_id=job_id,
                        ordinal=ordinal,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                )
            add_event(session, job_id, "segmented", {"segments": len(boundaries)})
            session.commit()

    def _terms(self, job: TranscriptionJob) -> tuple[list[str], dict[str, str]]:
        hotwords = list(job.hotwords or [])
        replacements = dict(job.replacements or {})
        if job.glossary_id:
            glossary = job.glossary
            if glossary:
                hotwords = list(glossary.hotwords or []) + hotwords
                replacements = {**dict(glossary.replacements or {}), **replacements}
        return list(dict.fromkeys(hotwords)), replacements

    def _next_batch(self, session: Session, job_id: str) -> list[TranscriptionSegment]:
        pending = session.scalars(
            select(TranscriptionSegment)
            .where(
                TranscriptionSegment.job_id == job_id,
                TranscriptionSegment.status != SegmentStatus.COMPLETED.value,
            )
            .order_by(TranscriptionSegment.ordinal)
        ).all()
        selected: list[TranscriptionSegment] = []
        duration_ms = 0
        for segment in pending:
            segment_ms = segment.end_ms - segment.start_ms
            if selected and (
                duration_ms + segment_ms > self.settings.asr_batch_seconds * 1000
                or len(selected) >= self.settings.asr_batch_max_segments
            ):
                break
            selected.append(segment)
            duration_ms += segment_ms
        return selected

    def _transcribe(self, job_id: str, audio_path: Path) -> None:
        assert self.asr_engine is not None
        while True:
            self._require_not_cancelled(job_id)
            with self.sessions() as session:
                job = session.get(TranscriptionJob, job_id)
                if job is None:
                    raise RuntimeError("job disappeared")
                batch = self._next_batch(session, job_id)
                if not batch:
                    return
                boundaries = [(segment.start_ms, segment.end_ms) for segment in batch]
                hotwords, replacements = self._terms(job)

            audio = read_audio_segments(audio_path, boundaries)
            last_error: Exception | None = None
            for attempt in range(self.settings.asr_batch_retries + 1):
                try:
                    texts = self.asr_engine.transcribe_batch(audio, hotwords=hotwords)
                    if len(texts) != len(batch):
                        raise RuntimeError(
                            f"ASR returned {len(texts)} results for {len(batch)} segments"
                        )
                    last_error = None
                    break
                except Exception as error:
                    last_error = error
                    logger.warning(
                        "ASR batch attempt failed",
                        extra={"job_id": job_id, "attempt": attempt + 1},
                    )
                    if attempt < self.settings.asr_batch_retries:
                        time.sleep(min(2**attempt, 5))
            if last_error is not None:
                with self.sessions() as session:
                    for segment in batch:
                        current = session.get(TranscriptionSegment, segment.id)
                        if current:
                            current.attempts += self.settings.asr_batch_retries + 1
                            current.status = SegmentStatus.FAILED.value
                            session.add(current)
                    session.commit()
                raise last_error

            with self.sessions() as session:
                for segment, raw_text in zip(batch, texts, strict=True):
                    current = session.get(TranscriptionSegment, segment.id)
                    if current is None:
                        raise RuntimeError("segment disappeared")
                    text, matches = apply_terminology(
                        raw_text,
                        hotwords=hotwords,
                        replacements=replacements,
                        fuzzy_threshold=self.settings.fuzzy_hotword_threshold,
                    )
                    current.text = text
                    current.replacements = matches
                    current.attempts += 1
                    current.status = SegmentStatus.COMPLETED.value
                    session.add(current)

                all_segments = session.scalars(
                    select(TranscriptionSegment)
                    .where(TranscriptionSegment.job_id == job_id)
                    .order_by(TranscriptionSegment.ordinal)
                ).all()
                completed = [
                    item for item in all_segments if item.status == SegmentStatus.COMPLETED.value
                ]
                total_speech = sum((item.end_ms - item.start_ms) / 1000 for item in all_segments)
                processed = sum((item.end_ms - item.start_ms) / 1000 for item in completed)
                partial = "".join(item.text for item in completed)
                job = session.get(TranscriptionJob, job_id)
                if job is None:
                    raise RuntimeError("job disappeared")
                fraction = processed / total_speech if total_speech else 1.0
                set_job_progress(
                    session,
                    job,
                    stage=JobStage.TRANSCRIBING,
                    progress=15 + fraction * 80,
                    processed_seconds=processed,
                    partial_text=(
                        partial[-self.settings.partial_text_chars :]
                        if self.settings.partial_text_chars
                        else ""
                    ),
                )
                session.commit()

    def _finish(self, job_id: str) -> None:
        assert self.asr_engine is not None
        self._require_not_cancelled(job_id)
        self._update_progress(job_id, JobStage.POSTPROCESSING, 95.0)
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job disappeared")
            segments = session.scalars(
                select(TranscriptionSegment)
                .where(TranscriptionSegment.job_id == job_id)
                .order_by(TranscriptionSegment.ordinal)
            ).all()
            joined = "".join(segment.text for segment in segments)
            hotwords, replacements = self._terms(job)

        punctuated = self.asr_engine.punctuate(joined)
        final_text, final_matches = apply_terminology(
            punctuated,
            hotwords=hotwords,
            replacements=replacements,
            fuzzy_threshold=self.settings.fuzzy_hotword_threshold,
        )
        result_dir = self.settings.results_dir / job_id
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "transcript.txt").write_text(final_text, encoding="utf-8")

        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job disappeared")
            segments = session.scalars(
                select(TranscriptionSegment)
                .where(TranscriptionSegment.job_id == job_id)
                .order_by(TranscriptionSegment.ordinal)
            ).all()
            metadata = {
                "model": self.settings.paraformer_model,
                "segments": len(segments),
                "terminology_matches": final_matches,
                "timestamps_are_approximate": True,
            }
            (result_dir / "result.json").write_text(
                json.dumps(
                    {
                        "id": job_id,
                        "text": final_text,
                        "segments": [
                            {
                                "ordinal": item.ordinal,
                                "start_ms": item.start_ms,
                                "end_ms": item.end_ms,
                                "text": item.text,
                                "replacements": item.replacements,
                            }
                            for item in segments
                        ],
                        "metadata": metadata,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            job.result_text = final_text
            job.result_metadata = metadata
            mark_terminal(
                session,
                job,
                status=JobStatus.SUCCEEDED,
                stage=JobStage.SUCCEEDED,
            )
            add_event(session, job_id, "succeeded", {"characters": len(final_text)})
            session.commit()

    def process_job(self, job_id: str) -> None:
        self._require_not_cancelled(job_id)
        with self.sessions() as session:
            job = session.get(TranscriptionJob, job_id)
            if job is None:
                raise RuntimeError("job not found")
            add_event(session, job_id, "started", {"worker_id": self.worker_id})
            session.commit()
        source = self._resolve_source(job_id)
        audio_path, duration = self._ensure_audio(job_id, source)
        self._ensure_segments(job_id, audio_path, duration)
        self._transcribe(job_id, audio_path)
        self._finish(job_id)
        if self.settings.cleanup_work_files:
            work_dir = self.settings.work_dir / job_id
            if work_dir.is_dir():
                # Keep downloaded URL sources for resumability only until success.
                shutil.rmtree(work_dir)

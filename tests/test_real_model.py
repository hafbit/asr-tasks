from __future__ import annotations

import os
from pathlib import Path

import pytest
import soundfile as sf

from asr_tasks.asr_engine import FunASREngine
from asr_tasks.config import Settings


@pytest.mark.model
def test_contextual_model_improves_configured_term_recall(tmp_path: Path) -> None:
    if os.getenv("ASR_RUN_MODEL_TESTS") != "1":
        pytest.skip("set ASR_RUN_MODEL_TESTS=1 to run real models")
    audio_path = os.getenv("ASR_MODEL_TEST_AUDIO")
    expected_term = os.getenv("ASR_MODEL_TEST_EXPECTED_TERM")
    if not audio_path or not expected_term:
        pytest.skip("ASR_MODEL_TEST_AUDIO and ASR_MODEL_TEST_EXPECTED_TERM are required")

    audio, sample_rate = sf.read(audio_path, dtype="float32")
    if sample_rate != 16_000:
        pytest.skip("the model acceptance sample must be 16 kHz")
    settings = Settings(
        data_dir=tmp_path,
        allow_unauthenticated=True,
        worker_count=1,
        cpu_threads=1,
    )
    settings.ensure_directories()
    engine = FunASREngine(settings)
    baseline = engine.transcribe_batch([audio], hotwords=[])[0]
    contextual = engine.transcribe_batch([audio], hotwords=[expected_term])[0]
    assert expected_term in contextual
    assert baseline.count(expected_term) <= contextual.count(expected_term)

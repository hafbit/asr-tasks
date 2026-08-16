from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Settings


class ASREngine(Protocol):
    def detect_speech(self, audio: np.ndarray, sample_rate: int) -> list[tuple[int, int]]: ...

    def transcribe_batch(
        self, audio: Sequence[np.ndarray], *, hotwords: list[str]
    ) -> list[str]: ...

    def punctuate(self, text: str) -> str: ...


class FunASREngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        cache = str(settings.models_dir)
        os.environ.setdefault("MODELSCOPE_CACHE", cache)
        os.environ.setdefault("HF_HOME", cache)
        os.environ.setdefault("TORCH_HOME", str(Path(cache) / "torch"))
        if settings.offline_mode:
            os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            for name, value in (
                ("Paraformer", settings.paraformer_model),
                ("VAD", settings.vad_model),
                ("punctuation", settings.punctuation_model),
            ):
                if not Path(value).is_dir():
                    raise RuntimeError(f"offline {name} model directory is missing: {value}")

        try:
            from funasr import AutoModel
        except ImportError as error:
            raise RuntimeError(
                "FunASR model dependencies are missing; install requirements-model.txt"
            ) from error

        common = {
            "device": "cpu",
            "ncpu": settings.cpu_threads,
            "hub": settings.model_hub,
            "disable_update": settings.disable_model_update,
        }
        self.vad = AutoModel(
            model=settings.vad_model,
            model_revision=settings.vad_model_revision,
            vad_kwargs={"max_single_segment_time": settings.vad_max_segment_ms},
            **common,
        )
        self.asr = AutoModel(
            model=settings.paraformer_model,
            model_revision=settings.paraformer_model_revision,
            **common,
        )
        self.punctuation = AutoModel(
            model=settings.punctuation_model,
            model_revision=settings.punctuation_model_revision,
            **common,
        )

    def detect_speech(self, audio: np.ndarray, sample_rate: int) -> list[tuple[int, int]]:
        result = self.vad.generate(
            input=audio,
            fs=sample_rate,
            disable_pbar=True,
            max_single_segment_time=self.settings.vad_max_segment_ms,
        )
        if not result:
            return []
        values = result[0].get("value") or []
        return [(int(item[0]), int(item[1])) for item in values if len(item) >= 2]

    def transcribe_batch(self, audio: Sequence[np.ndarray], *, hotwords: list[str]) -> list[str]:
        if not audio:
            return []
        kwargs: dict = {
            "input": list(audio),
            "batch_size_s": self.settings.asr_batch_seconds,
            "disable_pbar": True,
        }
        if hotwords:
            kwargs["hotword"] = " ".join(hotwords)
        results = self.asr.generate(**kwargs)
        return [str(item.get("text") or "").strip() for item in results]

    def punctuate(self, text: str) -> str:
        if not text.strip():
            return ""
        chunks = [text[index : index + 500] for index in range(0, len(text), 500)]
        output: list[str] = []
        for chunk in chunks:
            result = self.punctuation.generate(input=chunk, disable_pbar=True)
            output.append(str(result[0].get("text") or chunk) if result else chunk)
        return "".join(output)

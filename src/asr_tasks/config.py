from __future__ import annotations

import os
import subprocess
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def physical_cpu_count() -> int:
    """Return the physical core count where it can be determined without dependencies."""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        pairs: set[tuple[str, str]] = set()
        physical_id = core_id = None
        for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines() + [""]:
            if not line.strip():
                if physical_id is not None and core_id is not None:
                    pairs.add((physical_id, core_id))
                physical_id = core_id = None
            elif line.startswith("physical id"):
                physical_id = line.partition(":")[2].strip()
            elif line.startswith("core id"):
                core_id = line.partition(":")[2].strip()
        if pairs:
            return len(pairs)
    if os.name == "posix":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.physicalcpu"],
                check=True,
                capture_output=True,
                text=True,
            )
            return max(1, int(result.stdout.strip()))
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
            pass
    return max(1, os.cpu_count() or 1)


def _default_cpu_threads() -> int:
    return min(physical_cpu_count(), 8)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASR_", case_sensitive=False)

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    data_dir: Path = Path("/data")
    database_url: str | None = None
    api_token: str | None = None
    allow_unauthenticated: bool = False

    worker_count: int = Field(default=1, ge=1, le=4)
    cpu_threads: int = Field(default_factory=_default_cpu_threads, ge=1)
    poll_interval_seconds: float = Field(default=1.0, ge=0.1)
    heartbeat_interval_seconds: int = Field(default=15, ge=2)
    worker_stale_seconds: int = Field(default=90, ge=10)

    max_upload_bytes: int = Field(default=20 * 1024**3, ge=1)
    download_timeout_seconds: int = Field(default=3600, ge=10)
    source_url_allowed_hosts: list[str] = Field(default_factory=list)
    source_url_max_redirects: int = Field(default=5, ge=0, le=10)

    window_seconds: int = Field(default=1200, ge=60)
    window_overlap_seconds: int = Field(default=5, ge=0)
    vad_max_segment_ms: int = Field(default=30_000, ge=1_000)
    asr_batch_seconds: int = Field(default=60, ge=1)
    asr_batch_max_segments: int = Field(default=32, ge=1)
    asr_batch_retries: int = Field(default=2, ge=0, le=10)
    fuzzy_hotword_threshold: int = Field(default=90, ge=0, le=100)

    paraformer_model: str = (
        "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404"
    )
    paraformer_model_revision: str = "v2.0.5"
    vad_model: str = "fsmn-vad"
    vad_model_revision: str = "v2.0.4"
    punctuation_model: str = "ct-punc"
    punctuation_model_revision: str = "v2.0.4"
    model_hub: str = "ms"
    model_cache_dir: Path | None = None
    disable_model_update: bool = True
    offline_mode: bool = False

    partial_text_chars: int = Field(default=2_000, ge=0)
    cleanup_work_files: bool = True
    result_retention_days: int = Field(default=7, ge=1)

    @field_validator("source_url_allowed_hosts", mode="before")
    @classmethod
    def parse_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_runtime_budget(self) -> Settings:
        if self.window_overlap_seconds * 2 >= self.window_seconds:
            raise ValueError("window_overlap_seconds must be less than half window_seconds")
        available = physical_cpu_count()
        if self.worker_count * self.cpu_threads > available:
            raise ValueError(
                "worker_count * cpu_threads must not exceed available physical CPU count "
                f"({available})"
            )
        return self

    @cached_property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.database_path}"

    @cached_property
    def database_path(self) -> Path:
        return self.data_dir / "db" / "asr-tasks.sqlite3"

    @cached_property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @cached_property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @cached_property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @cached_property
    def models_dir(self) -> Path:
        return self.model_cache_dir or self.data_dir / "models"

    def ensure_directories(self) -> None:
        for path in (
            self.database_path.parent,
            self.assets_dir,
            self.work_dir,
            self.results_dir,
            self.models_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asr_tasks.api import create_app
from asr_tasks.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        api_token="test-token",
        worker_count=1,
        cpu_threads=1,
        heartbeat_interval_seconds=2,
        worker_stale_seconds=10,
        window_seconds=60,
        window_overlap_seconds=2,
        asr_batch_seconds=10,
        source_url_allowed_hosts=["example.com"],
    )


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(settings)


@pytest.fixture
def client(app):  # type: ignore[no-untyped-def]
    with TestClient(app) as value:
        yield value


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
